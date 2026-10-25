# Windows 权限与完整性级别排查（`PermissionError: ~/.desk` 写不进去）

本文记录一类**看起来像权限问题、实际是「进程完整性级别」问题**的故障：ACL 一切正常、没有进程占用、
也没装杀软，但 `uv run desk-core` 一启动就崩在写 `~/.desk` 上。

典型报错（报错点会随执行顺序变化，本质都是写 `~/.desk` 被拒）：

```
PermissionError: [Errno 13] Permission denied: 'C:\Users\<user>\.desk\logs\core.log'
PermissionError: [Errno 13] Permission denied: 'C:\Users\<user>\.desk\run\desk.key'
PermissionError: [WinError 5] 拒绝访问。: 'C:\Users\<user>\.desk\sessions'
PermissionError: [Errno 13] Permission denied: 'C:\Users\<user>\.desk\traces\daemon.jsonl'
```

`desk-core` 启动路径上任何一次写盘都会先炸，所以崩溃位置不同不代表原因不同：

| 崩溃点 | 写入目标 |
|--------|----------|
| `core/logging_setup.py` → `setup_logging` | `logs/core.log` |
| `core/transport/tls.py` → `generate_self_signed` | `run/desk.key`、`run/desk.crt` |
| `core/trace/writer.py` → `TraceWriter._drain` | `traces/daemon.jsonl` |
| `core/session/store.py` → `SessionStore.__init__` | `sessions/`（`mkdir`） |

---

## TL;DR：一条命令修好

```powershell
icacls "DeskAgent\" /setintegritylevel "(OI)(CI)M" /c /q
icacls "DeskAgent\.venv" /setintegritylevel "(OI)(CI)M" /t /c /q
uv run desk-core
```

> **括号必须加引号。** 直接写 `(OI)(CI)M` 会被 PowerShell 当成子表达式解析，报
> `无法将"OI"项识别为 cmdlet`。
>
> 不需要管理员权限（以文件属主身份即可）；7700 个文件约 2 秒；命令幂等，可反复执行。

---

## 原理：ACL 之外还有一层「完整性级别」

Windows 的访问控制有两道闸：

1. **DACL（自由访问控制列表）** —— `FullControl` / `Read` / `Deny` 这些，`icacls`、`Get-Acl` 看到的就是它。
2. **强制完整性级别（Mandatory Integrity Level，IL）** —— 存在对象的 SACL 里（`Get-Acl -Audit` 才看得到，
   且需要管理员权限），取值 Low(0x1000) < Medium(0x2000) < High(0x3000) < System(0x4000)。

IL 的规则是**「只能往同级或更低级写」**：

| 进程 IL | 读 Medium 对象 | 写 Medium 对象 |
|---------|----------------|----------------|
| Medium（普通终端、`python.exe` 默认） | ✅ | ✅ |
| **Low** | ✅ | ❌ `ACCESS_DENIED` |

所以当一个 Low 进程去写 Medium 的 `~/.desk` 时：**读得动、写得死、ACL 完全正常、也查不到任何占用**。
这正是本故障最迷惑人的地方。

### 为什么进程会变成 Low

Windows 启动一个 **被标为 Low 的 exe** 时，进程也跟着以 Low 运行。也就是说：把 `python.exe`
复制到带 Low 标签的目录里，它就跑成 Low；同一个二进制复制到普通目录里，它就跑成 Medium。
实测（同一个 `python.exe` 二进制，只改位置）：

| 可执行文件位置 | 进程 IL |
|----------------|---------|
| `Python\python.exe` | MEDIUM |
| `D:\p_dir\p_b.exe`（复制到普通目录） | MEDIUM |
| `DeskAgent\p_a.exe`（复制到项目根） | **LOW** |
| `DeskAgent\.venv\Scripts\p_c.exe` | **LOW** |
| 同上，执行 `icacls ... /setintegritylevel M` 之后 | MEDIUM |

由此推断：**项目目录 `DeskAgent` 被打了可继承的 Low 标签**，于是
`.venv\Scripts\python.exe`、`desk-core.exe` 等全部继承 Low，任何 `uv run` 启动的进程都是 Low，
写 `~/.desk`（Medium）必然被拒。PowerShell 自己在 `C:\Windows\System32`（Medium），所以
「PowerShell 能写、python 不能写」——这也是最快的判定信号。

### Low 标签是怎么来的

常见于**沙箱化的工具**为了把低权限子进程限制在项目内，对工作区执行了等价于
`icacls <工作区> /setintegritylevel L` 的操作（部分 AI 编码代理、容器/沙箱工具会这么做）。
这个标签是**落在磁盘上的持久属性**，工具退出后依然生效，所以之后在 VSCode 里普通终端跑也会失败。

---

## 三步定位

### 第 1 步：确认「同一个路径，谁能写谁不能写」

```powershell
# 终端 A：PowerShell（Medium）—— 通常成功
[IO.File]::WriteAllText("$env:USERPROFILE\.desk\logs\ps_probe.txt", "x")

# 终端 B：项目解释器 —— 若失败，说明是「进程身份」而不是「目录权限」
uv run python -c "import pathlib; pathlib.Path.home().joinpath('.desk','logs','py_probe.txt').write_text('x')"
```

判读：

- PowerShell 成功 + python 失败 → **进程级差异**，继续第 2 步（大概率是 IL）。
- 两个都失败 → 才轮到查 ACL / 占用 / 杀软（见文末「其它成因」表）。

### 第 2 步：看进程自己的 IL

```powershell
whoami /groups | Select-String 'S-1-16'
```

输出里的 SID 就是当前进程的完整性级别：

| SID | 级别 | 说明 |
|-----|------|------|
| `S-1-16-4096` | **LOW** | 被沙箱化/降权，写不了 `~/.desk` |
| `S-1-16-8192` | MEDIUM | 正常用户进程 |
| `S-1-16-12288` | HIGH | 管理员提权进程 |

想看 python 子进程的 IL，用同一条命令从 python 里调：

```powershell
uv run python -c "import subprocess;print(subprocess.run(['whoami','/groups'],capture_output=True).stdout.decode('oem','replace'))" | Select-String 'S-1-16'
```

### 第 3 步：看目录/文件的标签（需管理员 PowerShell）

```powershell
(Get-Acl -Audit "DeskAgent").Audit |
  Format-Table IdentityReference, FileSystemRights, AccessControlType
```

会看到 `Mandatory Label\Low Mandatory Level`（或 `低强制级别`）一类条目。
普通权限读不到 SACL，会报「该进程不具有执行此操作所需的 SeSecurityPrivilege 特权」——这属正常，
改用第 2 步的进程 IL 判断即可。

---

## 修复

### 场景一：整个工作区被打了 Low 标签（最常见）

```powershell
# 项目根：修正自身标签，并让以后新建的文件/目录继承 Medium
icacls "DeskAgent" /setintegritylevel "(OI)(CI)M" /c /q

# .venv：递归修正已有的 7700+ 个文件（python.exe / 各命令行脚本都在这里）
icacls "DeskAgent\.venv" /setintegritylevel "(OI)(CI)M" /t /c /q
```

需要连 `src/`、`tests/` 一起归位时，把第二条的 `.venv` 换成 `DeskAgent` 并保留 `/t`。

参数说明：

| 参数 | 含义 |
|------|------|
| `(OI)` | Object Inherit：子文件继承 |
| `(CI)` | Container Inherit：子目录继承 |
| `M` | 设为 Medium（`L`=Low、`M`=Medium、`H`=High） |
| `/t` | 递归处理 |
| `/c` | 单个失败继续处理其余 |
| `/q` | 安静模式，只输出汇总 |

### 场景二：只想修最小范围

只修 python 与命令行入口即可（`desk-core.exe` 这类 trampoline 也必须一起修，否则它仍以 Low 启动，
子进程继承 Low token）：

```powershell
icacls "DeskAgent\.venv\Scripts" /setintegritylevel "(OI)(CI)M" /t /c /q
```

### 场景三：ACL 也确实坏了（少见）

标签修正后仍报错，才需要接管 + 重置 DACL：

```powershell
# 管理员 PowerShell
$d = "$env:USERPROFILE\.desk"
takeown /f $d /r /d y
icacls $d /reset /t /c /q
icacls $d /grant "$env:USERNAME:(OI)(CI)F" /t /c /q
icacls $d /setintegritylevel "(OI)(CI)M" /t /c /q
```

---

## 验证

```powershell
# 1) 解释器写 ~/.desk 不再报错
uv run python -c "import pathlib;p=pathlib.Path.home()/'.desk'/'logs'/'core.log';p.parent.mkdir(parents=True,exist_ok=True);p.open('a').close();print('write OK')"

# 2) 守护进程正常起
uv run desk-core
```

期望看到：

```
level=INFO ... msg="desk-core 0.1.0 listening addr=127.0.0.1:9986 auth=True tls=True"
```

并且 `~/.desk` 下这几个文件被正常创建/更新：

```
~/.desk/logs/core.log
~/.desk/run/desk.key + desk.crt + daemon.json
~/.desk/traces/daemon.jsonl
```

若 `~/.desk/run/daemon.json` 是上一次崩溃残留（PID 早已不存在），可先删掉：

```powershell
Remove-Item "$env:USERPROFILE\.desk\run\daemon.json" -Force
```

---

## 复发预防

- 任何沙箱化工具/代理再对工作区执行一次「打 Low 标签」，故障就会复发（症状一模一样：
  `Errno 13` 写 `~/.desk`）。把本文 TL;DR 的两条命令存成脚本即可秒恢复。
- **别删 `~/.desk` 来"重来"**：目录被删后 Low 进程连"新建"都做不到，报错变成
  `mkdir ... PermissionError`，更容易误判成"权限坏了"。历史会话也会一并丢失。
- **别用管理员身份跑 `desk-core`**：会在 `~/.desk` 下生成 **High** 标签的文件，之后普通
  （Medium）进程又变成"只能读不能写"，制造反向的同类问题。
- **别急着重装/重建 `.venv`**：本故障与 venv 无关。顺带记一笔：`uv` 0.12.x 建 venv 时
  `Scripts\python.exe` 是 `py.exe`（约 270 KB、`OriginalFilename=py.exe`），这是正常行为，
  不是文件被替换。

---

## 其它成因（先排除，再动手）

| 现象 | 判定方法 | 处理 |
|------|----------|------|
| 进程 IL = LOW | `whoami /groups \| Select-String 'S-1-16'` → `S-1-16-4096` | 本文 TL;DR |
| 文件被别的进程独占 | `[IO.File]::Open($p,'Open','Read','None')` 抛 `IOException`（"正由另一进程使用"） | 结束旧 `desk-core`：`Get-Process desk-core,python -EA 0 \| Stop-Process -Force` |
| 只读属性 | `Get-Item $p \| Select-Object Attributes` 含 `ReadOnly` | `attrib -R $p` / `Set-ItemProperty $p IsReadOnly $false` |
| DACL 缺写权限 | `icacls $p` 里当前用户不是 `(F)`/`(M)` | 场景三 |
| Defender 受控文件夹访问 | 安全中心 → 病毒和威胁防护 → 勒索软件防护 → 受控文件夹访问；把 `python.exe` 加入允许列表 | 关闭该功能或加白 |
| 磁盘满 / 目录不存在 | 报错为 `ENOSPC` / `FileNotFoundError`，而非 `Errno 13` | 清盘 / 确认路径 |

---

## 附：本次事件的证据链（可复现）

1. 三个失败文件 `core.log` / `desk.key` / `daemon.jsonl` 属性均为 `Archive`（非只读），属主为当前用户，
   DACL 含 `SYSTEM` / `Administrators` / 当前用户 三条 `Allow FullControl`，无 `Deny`。
2. 以 `FileShare.None` 独占打开三个文件均成功 → 无任何进程占用。
3. 系统中无第三方杀软；Defender 受控文件夹访问未配置保护目录。
4. 删除 `~/.desk` 后仍无法创建 → 与 `.desk` 自身无关。
5. 同一条写操作：PowerShell 成功、项目 `python` 失败。
6. `whoami /groups`：PowerShell = MEDIUM，项目 `python` = **LOW**。
7. 同一 `python.exe` 二进制放入项目目录 → LOW；复制到项目外的普通目录 → MEDIUM；
   对该副本执行 `icacls /setintegritylevel M` → 立即变 MEDIUM。
8. 对工作区与 `.venv` 递归执行 `icacls /setintegritylevel "(OI)(CI)M"` 后：
   `desk-core` 正常监听 `127.0.0.1:9986`，`~/.desk` 下 5 个文件全部写入成功。

---

## 附带：`Activate.ps1` 无法加载（执行策略）

与上面的权限问题无关，但常在同一个终端里一起出现：

```
无法加载文件 ...\.venv\Scripts\Activate.ps1。未对文件 ... 进行数字签名。无法在当前系统上运行该脚本。
```

- **首选方案：不要激活。** `uv run <命令>` 会自己使用 `.venv`，无需 `Activate.ps1`。
  也可直接调 `.venv\Scripts\python.exe`。
- 需要激活时，把执行策略改成持久的（免管理员、只影响当前用户），然后**重开终端**：

  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
  ```

- 文件带「从 Internet 下载」标记（MOTW）时，`RemoteSigned` 仍会要求签名：

  ```powershell
  Unblock-File .venv\Scripts\Activate.ps1
  ```

- `-Scope Process` **只对设置它的那一个 PowerShell 进程生效**，VSCode 自动激活/新开终端都会重置。
- 让 VSCode 不再自动激活（本项目用 `uv run`，不需要它）：

  ```jsonc
  "python.terminal.activateEnvironment": false
  ```

- 诊断（在报错的那个窗口里执行）：

  ```powershell
  Get-ExecutionPolicy -List     # MachinePolicy/UserPolicy 有值 = 组策略强制，-Scope Process 无效
  [Environment]::Is64BitProcess # 32 位宿主读的是另一份策略分支
  ```
