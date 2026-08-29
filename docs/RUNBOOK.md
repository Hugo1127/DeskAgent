# 运维手册（RUNBOOK）

## 日常操作

### 启动守护进程

```bash
uv run desk-core
```

默认监听 `127.0.0.1:9986`，按 `Ctrl+C` 优雅退出。

### 验证连通

```bash
uv run desk ping
# → pong server=0.0.1 uptime=12ms latency=2ms
```

### 停止守护进程

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

## 个人桌面管家智能体（DeskAgent）

DeskAgent 可作为**个人桌面管家智能体**使用：用自然语言一句话指挥电脑完成监控、清理、卸载、进程管理等任务。

> 以下管家相关路径为**规划中**内容，随系统管理工具集落地后生效：

| 路径 | 说明 |
|------|------|
| `~/.desk/clean_whitelist.toml` | 清理/卸载的白名单保护配置 |
| `~/.desk/audit/operations.jsonl` | 破坏性操作（清理/卸载/结束进程）审计日志 |
| `~/.desk/config.toml` 的 `[monitor]` 段 | 7×24 监控采集间隔与告警阈值 |

**运维要点**：清理、卸载、结束进程均为不可逆的破坏性操作，默认 dry-run 预览 + 权限审批 + 白名单保护，生产使用务必先在测试机/虚拟机验证。

---

## 日志

```bash
tail -f ~/.desk/logs/core.log
```

---

## 权限 dry-run 预览的 bash 依赖

审批预览的 bash 语法检查（`bash -n`，只解析不执行）需要可执行的 bash。daemon 启动时按以下顺序探测：

1. `DESK_BASH_PATH` 环境变量（显式指定 bash.exe 路径）
2. `PATH` 中的 `bash`
3. Git for Windows 常见安装位置（`C:\Program Files\Git\bin\bash.exe` 等）

Windows 上通常随 Git for Windows 自带 Git Bash；**若 Git 安装在非默认位置（如 `D:\Programs\Git`），自动探测会 miss**，预览提示「⚠ 未找到 bash，跳过语法检查」——此时决策链轨迹、危险特征提示、diff 预览仍正常，仅语法检查不可用。

**解决**：启动 daemon 时带上环境变量：

```bash
DESK_BASH_PATH="D:\Programs\Git\usr\bin\bash.exe" uv run desk-core
```

或将 Git 的 `usr/bin` 加入系统 PATH 后重启 daemon。

> **待办（后续处理）**：自动从 `git --exec-path` 推导 Git 安装根目录，覆盖非默认安装路径；可选：让 `BashTool` 在 Windows 上实际执行也走 Git Bash，使语法检查与真实执行环境一致。

---

## 常见错误

| 报错 | 原因 | 处理 |
|------|------|------|
| `already running  (127.0.0.1:9986)` | 已有守护进程在运行 | `kill $(pgrep -f desk-core)` |
| `error: core not running (127.0.0.1:9986)` | 未启动守护进程 | `uv run desk-core` |
| `Address already in use` | 端口被其他进程占用 | `DESK_PORT=9987 uv run desk-core` |
| `Config error: DESK_PORT must be an integer` | `.env` 或环境变量中端口值非整数 | 检查 `DESK_PORT` 的值 |
| 预览提示「未找到 bash，跳过语法检查」 | daemon 环境 PATH 无 bash，且 Git 装在非默认位置（自动探测 miss） | 见上文「权限 dry-run 预览的 bash 依赖」：设置 `DESK_BASH_PATH` 或加入系统 PATH 后重启 daemon |
