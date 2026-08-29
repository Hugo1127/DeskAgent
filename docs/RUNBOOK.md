# 运维手册（RUNBOOK）

## 日常操作

### 启动守护进程

```bash
uv run desk-core            # 前台运行（推荐开发/调试）
uv run desk core start      # 后台静默运行，desk core stop 停止
```

默认监听 `127.0.0.1:9986`，按 `Ctrl+C` 优雅退出（前台模式）。
启动后连接信息（令牌 / 端口 / 证书指纹）写入 `~/.desk/run/daemon.json`，
客户端据此自动认证连接；令牌认证与 TLS 默认开启（见 [COMMUNICATION_SECURITY.md](COMMUNICATION_SECURITY.md)）。

### 验证连通

```bash
uv run desk ping
# → pong server=0.0.1 uptime=12ms latency=2ms
```

### 停止守护进程

```bash
uv run desk core stop        # 推荐：读 run 文件取 PID，优雅停止并清理 run 文件
```

兜底（手动杀进程）：

```bash
kill $(pgrep -f desk-core)
```

---

## 配置

优先级（低 → 高）：**内建默认值 → `~/.desk/config.toml` → `.desk/config.toml` → `.env` → 系统环境变量**。

### `~/.desk/config.toml`

```toml
[core]
host = "127.0.0.1"
port = 9986

[logging]
level  = "INFO"
file   = "~/.desk/logs/core.log"
format = "text"    # "text" | "json"
```

### `.env`

从 `.env.example` 复制后修改，存放本机配置与密钥（不提交 git）：

```bash
cp .env.example .env
```

### 系统环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DESK_CONFIG` | `~/.desk/config.toml` | 覆盖配置文件路径 |
| `DESK_HOST` | `127.0.0.1` | TCP 监听地址 |
| `DESK_PORT` | `9986` | TCP 监听端口 |
| `DESK_IPC_AUTH` | `true` | IPC 令牌握手认证（`0` 关闭，仅调试用） |
| `DESK_IPC_TLS` | `true` | IPC TLS 传输加密（自签证书 + 证书固定；`0` 关闭，仅调试用） |
| `DESK_LOG_LEVEL` | `INFO` | 日志级别（DEBUG / INFO / WARNING / ERROR） |
| `DESK_LOG_FILE` | `~/.desk/logs/core.log` | 日志文件路径（留空则仅输出 stderr） |
| `DESK_LOG_FORMAT` | `text` | 日志格式（`text` 或 `json`） |
| `DESK_BASH_PATH` | 空 | 显式指定 bash.exe 路径（dry-run 语法检查用，优先级最高） |

---

## 开发

```bash
uv run ruff check src tests scripts   # lint
uv run mypy src                       # 类型检查
uv run pytest tests/ -v               # 全量测试
uv run pytest tests/unit/ -v         # 仅单元测试（无需启动 daemon）
uv run python scripts/gen_protocol_doc.py          # 重新生成 WIRE_PROTOCOL.md
uv run python scripts/gen_protocol_doc.py --check  # 校验协议文档与代码同源
```

---

## 个人桌面管家智能体（规划中）

DeskAgent 定位为**个人桌面管家智能体**：用自然语言一句话指挥电脑完成监控、清理、卸载、进程管理等任务。
清理 / 卸载 / 结束进程均为不可逆的破坏性操作，将默认 dry-run 预览 + 权限审批 + 白名单保护。
相关配置路径（`~/.desk/clean_whitelist.toml`、`~/.desk/audit/operations.jsonl`、`[monitor]` 段）为**规划中**内容，随系统管理工具集落地后生效。

---

## 日志

```bash
tail -f ~/.desk/logs/core.log
```

---

## 权限 dry-run 预览的 bash 依赖

审批预览的 bash 语法检查（`bash -n`，只解析不执行）需要可执行的 bash。daemon 按以下顺序探测：

1. `DESK_BASH_PATH` 环境变量（显式指定 bash.exe 路径）
2. `PATH` 中的 `bash`
3. Git for Windows 常见安装位置（`C:\Program Files\Git\bin\bash.exe` 等）
4. 从 `PATH` 中的 `git` 可执行文件位置向上推导 Git 安装根目录（无需启动子进程）
5. `git --exec-path` 输出推导安装根目录（最后兜底，覆盖 git 经 shim 调用的场景）

Windows 上通常随 Git for Windows 自带 Git Bash。即使 **Git 安装在非默认位置（如 `D:\Programs\Git`）**，只要 `git` 在 daemon 的 PATH 中，第 4/5 步会自动推导出安装根目录并找到其 `bin/bash.exe` 或 `usr/bin/bash.exe`，语法检查照常可用。

**手动指定**（优先级最高）：启动 daemon 时带上环境变量：

```bash
DESK_BASH_PATH="D:\Programs\Git\usr\bin\bash.exe" uv run desk-core
```

或将 Git 的 `usr/bin` 加入系统 PATH 后重启 daemon。

---

## 常见错误

| 报错 | 原因 | 处理 |
|------|------|------|
| `already running  (127.0.0.1:9986)` | 已有守护进程在运行 | `uv run desk core stop`（推荐）或 `kill $(pgrep -f desk-core)` |
| `error: core not running (127.0.0.1:9986)` | 未启动守护进程 | `uv run desk core start`（后台）或 `uv run desk-core`（前台） |
| `Address already in use` | 端口被其他进程占用 | `DESK_PORT=9987 uv run desk-core` |
| `Config error: DESK_PORT must be an integer` | `.env` 或环境变量中端口值非整数 | 检查 `DESK_PORT` 的值 |
| 预览提示「未找到 bash，跳过语法检查」 | daemon 环境 PATH 无 bash，且 git 不在 PATH（自动推导失效） | 见上文「权限 dry-run 预览的 bash 依赖」：确认 `git` 在 daemon 的 PATH 中（可自动推导非默认安装位置），或设置 `DESK_BASH_PATH` / 加入系统 PATH 后重启 daemon |
