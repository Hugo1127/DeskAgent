# 运维手册（RUNBOOK）

## 日常操作

### 启动守护进程

```bash
uv run desk-core
```

默认监听 `127.0.0.1:7437`，按 `Ctrl+C` 优雅退出。

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

优先级（低 → 高）：**内建默认值 → `~/.desk/config.toml` → `.env` → 系统环境变量**。

### `~/.desk/config.toml`

```toml
[core]
host = "127.0.0.1"
port = 7437

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
| `DESK_PORT` | `7437` | TCP 监听端口 |
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

make docs                             # 重新生成 WIRE_PROTOCOL.md
make verify-s0                        # 完整验证（lint + 类型 + 测试 + 协议同源检查）
```

---

## 个人桌面管家智能体（DeskAgent）

DeskAgent 可作为**个人桌面管家智能体**使用：用自然语言一句话指挥电脑完成监控、清理、卸载、进程管理等任务（场景说明见 [docs/个人桌面管家智能体.md](docs/个人桌面管家智能体.md)）。

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

## 常见错误

| 报错 | 原因 | 处理 |
|------|------|------|
| `core already running at 127.0.0.1:7437` | 已有守护进程在运行 | `kill $(pgrep -f desk-core)` |
| `core not running` | 未启动守护进程 | `uv run desk-core` |
| `Address already in use` | 端口被其他进程占用 | `DESK_PORT=8000 uv run desk-core` |
| `Config error: DESK_PORT must be an integer` | `.env` 或环境变量中端口值非整数 | 检查 `DESK_PORT` 的值 |
