# DeskAgent

DeskAgent 是一个**本地 AI Agent 运行时**（mini 版 Claude Code）：以常驻守护进程 + 多客户端架构运行，把「用户目标 → 任务拆解 → Agent Loop → 工具调用 → 权限审批 → 事件流 → 会话记忆 → 上下文压缩」整条链路落地，可接入 Claude 等大模型，支持 Skills、子 Agent 与 MCP 扩展。

> **项目来源**：本项目由 [KamaClaude](https://github.com/youngyangyang04/KamaClaude)（代码随想录：从零实现一个本地 Claude Code Agent 系统）**二次开发**而来——在原有 Agent 运行时内核之上，明确了「个人桌面管家」的产品定位，并持续补齐权限审批、Skills、子 Agent 编排、MCP 扩展、上下文压缩等工程能力。

## 特性

- **守护进程 + 多客户端**：`desk-core` 常驻后台，CLI / TUI 多客户端接入，断开不中断执行、重连可恢复订阅
- **ReAct AgentLoop**：plan → act → observe 迭代闭环，支持思考、多步执行、max_steps 兜底
- **类型化事件流**：Pydantic v2 定义命令与事件，EventBus 驱动，每次 run 落盘 `events.jsonl` 支持回放
- **四级权限审批**：`allow_once / always_allow / deny_once / always_deny`，bash 越界路径强制 ASK，决策持久化到 `policy.toml`
- **会话记忆 + 上下文治理**：Session / notes 分层延续上下文，水位检测、tool_result 截断、自动/手动压缩（`/compact`）
- **Skills**：Markdown front-matter 定义行为预设与工具白名单，三级查找覆盖，无需改代码扩展
- **子 Agent + MCP**：`spawn_agent` 前台/后台编排、事件桥接；MCP 客户端支持 stdio / TCP 接入外部工具
- **统一系统查询**：内置 `system_query` 工具，一个工具多类别（monitor 系统监控 / port 端口占用 / software 已安装软件），psutil + 注册表只读采集，零审批打扰
- **三层 Trace + Textual TUI**：IPC / Event / LLM 全链路落盘可回放；TUI 流式渲染、内联审批、Skill 补全

## 架构

```
┌──────────────┐      ┌──────────────┐
│   desk CLI   │      │   desk-tui   │
│   (客户端)    │      │   (客户端)    │
└──────┬───────┘      └──────┬───────┘
       │                     │
       │   JSON-RPC 2.0 over NDJSON TCP
       │   （默认 127.0.0.1:9986）
       ▼                     ▼
┌──────────────────────────────────────┐
│            desk-core 守护进程          │
│  ┌────────────────────────────────┐  │
│  │ AgentRunner → AgentLoop        │  │
│  │ （ReAct：plan → act → observe） │  │
│  │ ToolRegistry · PermissionMgr   │  │
│  │ EventBus · Session · Compactor │  │
│  │ Skills · Subagent · MCP        │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
```

## 快速开始

环境要求：Python 3.12（`>=3.12,<3.13`）+ [uv](https://docs.astral.sh/uv/)。

```bash
uv sync                                   # 1. 安装依赖
cp .env.example .env                      # 2. 配置 .env（填入 ANTHROPIC_API_KEY）
uv run desk core start                    # 3. 后台启动守护进程（静默，desk core stop 停止）
uv run desk ping                          # 4. 验证连通
uv run desk run --goal "总结 README.md 的主要章节"  # 5. 单次任务
uv run desk chat                          # 6. 多轮对话
uv run desk-tui                           # 7. 终端 UI
```

> **前台运行（推荐开发/调试）**：`uv run desk-core` 直接在前台启动守护进程，日志实时打到
> 终端、Ctrl+C 停止。与 `desk core start`（后台静默）二选一，两者互斥勿同时启动。

## 命令行

| 命令 | 说明 |
|------|------|
| `desk --version` | 打印版本号 |
| `desk ping` | 连通性检查（需 daemon 运行） |
| `desk core start / stop / status` | 守护进程管理（后台运行） |
| `desk-core` | 前台运行守护进程（日志进终端，Ctrl+C 停止） |
| `desk run --goal "<目标>"` | 单次运行 Agent 任务（事件流实时打印） |
| `desk chat` | 多轮会话，权限审批用 `y/a/n/d` 响应 |
| `desk trace [run_id]` | 查看 trace（`--layer` / `--direction` / `--raw` / `--follow`） |
| `desk-tui [--replay <run_id>]` | Textual 终端 UI（可回放历史 run） |

## 配置

优先级（低 → 高）：**内建默认值 → `~/.desk/config.toml` → `.desk/config.toml` → `.env` → 系统环境变量**。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DESK_CONFIG` | `~/.desk/config.toml` | 覆盖配置文件路径 |
| `DESK_HOST` / `DESK_PORT` | `127.0.0.1` / `9986` | TCP 监听地址 / 端口 |
| `DESK_IPC_AUTH` / `DESK_IPC_TLS` | `true` / `true` | IPC 令牌握手认证 / TLS 传输加密（安全默认开启，`0` 关闭仅调试用） |
| `DESK_LOG_LEVEL` / `DESK_LOG_FORMAT` | `INFO` / `text` | 日志级别 / 格式（`text` / `json`） |
| `DESK_LOG_FILE` | `~/.desk/logs/core.log` | 日志文件路径 |
| `DESK_MAX_STEPS` | `20` | Agent 单次 run 最大步数 |
| `DESK_LLM_DEFAULT_MODEL` | `claude-sonnet-4-6` | 默认 LLM 模型 |
| `DESK_TRACE_ENABLED` / `DESK_TRACE_FILE` | `true` / `~/.desk/traces/daemon.jsonl` | trace 开关 / 文件路径 |
| `DESK_PERMISSION_TIMEOUT_S` | `60` | 权限审批超时秒数（0 不超时） |
| `DESK_COMPACT_THRESHOLD` | `0.0` | 自动压缩水位（0 禁用，推荐手动 `/compact`） |
| `DESK_COMPACT_TOOL_LIMIT` / `DESK_COMPACT_TOOL_KEEP` | `8000` / `4000` | tool_result 截断阈值 / 保留前缀 |

### 配置文件示例（`~/.desk/config.toml`）

```toml
[core]
host = "127.0.0.1"
port = 9986
# IPC 通信安全（默认开启；auth=false/tls=false 仅调试用）
auth = true
tls = true

[llm]
default_model = "claude-sonnet-4-6"

[permission]
timeout_s = 60

[compaction]
auto_threshold = 0.0
tool_result_limit = 8000
tool_result_keep = 4000

[[mcp.servers]]
name = "example"
transport = "stdio"
command = "/path/to/mcp-server"
```

## 内置工具与权限

| 工具 | 说明 | 默认权限 |
|------|------|----------|
| `read_file` / `list_dir` | 读取文件 / 列出目录 | 允许 |
| `read_image` | 读取图片（png/jpg/jpeg/gif/webp，≤5MB），以视觉内容块返回，支持多模态识别（截图 / 图表 / 图片文字） | 允许 |
| `write_file` | 写入文件 | 询问 |
| `bash` | 执行 shell 命令（访问 cwd 之外路径**强制询问**） | 询问 |
| `system_query` | 统一查询工具：`category=monitor` 系统状态（CPU / 内存 / 磁盘 / 网络 / 进程 / 电池 / GPU / OS）、`category=port` 端口占用（PID / 进程 / kill 建议）、`category=software` 已安装软件（注册表，Windows，可用 `name_filter` 过滤） | 允许 |
| `note_save` | 保存会话笔记 | 允许 |
| `task_create` / `task_update` / `task_list` / `task_get` | 子任务管理 | 询问 |
| `spawn_agent` / `agent_result` | 派生子 Agent / 获取后台结果 | 询问 |

权限评估采用**静态策略分层**：deny_patterns → bash 越界路径启发式（不可绕过）→ session 级缓存 → 持久化缓存 → allow_patterns → 工具默认策略；命中 ASK 时推送事件给客户端，`always_allow` / `always_deny` 持久化到 `~/.desk/policy.toml`。

## Skills 与子 Agent

- **Skills**：Markdown front-matter 定义名称、描述、系统提示词与工具白名单，输入 `/skill名 参数` 触发；查找优先级：项目本地 `.desk/skills/` > 用户全局 `~/.desk/skills/` > 内建 `src/desk_agent/core/skills/builtin/`
- **子 Agent**：`spawn_agent` 在隔离冷启动上下文中派生子 Agent（前台阻塞 / 后台并行），事件桥接透传，TUI 嵌套展示；角色档案用 TOML 定义，优先级：项目本地 `.desk/agents/` > 用户全局 `~/.desk/agents/` > 内建
- **MCP**：通过 `[[mcp.servers]]` 配置 stdio / TCP 外部工具服务器，工具以 `{server}__{tool}` 前缀注册，失败记录日志不影响 Core 启动

## 数据目录

```
~/.desk/
├── config.toml / policy.toml / context.md   # 配置 / 权限决策 / 全局上下文
├── run/daemon.json + desk.crt + desk.key     # 运行信息（令牌 / 端口 / 证书指纹）
├── logs/  traces/daemon.jsonl               # 日志 / 三层 trace
└── sessions/<id>/  agents/  skills/         # 会话与 run 事件 / 用户级角色 / 用户级 Skill
```

## 项目结构

```
src/desk_agent/
├── cli/                    # desk 命令行客户端（ping / run / chat / core / trace）
├── core/
│   ├── app.py              # 守护进程入口（协议分发、订阅、生命周期）
│   ├── loop.py / runner.py # AgentLoop（ReAct 循环） / AgentRunner（组装运行链路）
│   ├── bus/ · events/      # 类型化命令/事件模型 + EventBus / events.jsonl
│   ├── transport/          # TCP server / client + IPC 事件广播
│   ├── tools/              # BaseTool + 内置工具 + 注册中心
│   ├── permissions/        # 四级权限审批（policy / manager / storage）
│   ├── session/ · compact/ # 会话存储 / 上下文压缩
│   ├── skills/ · subagent/ · agents/   # Skill 加载 / 子 Agent / 角色档案（TOML）
│   ├── mcp/ · trace/ · task/ · memory/ # MCP 客户端 / trace / 子任务 / 上下文加载
│   └── config.py           # 分层配置（TOML / .env / 环境变量）
└── tui/                    # Textual 终端 UI
```

## 开发

```bash
uv sync
uv run ruff check src tests scripts   # lint
uv run mypy src                       # 类型检查（strict）
uv run pytest tests/unit -v           # 单元测试
uv run pytest tests/integration -v    # 集成测试（双进程 / IPC）
uv run python scripts/gen_protocol_doc.py          # 重新生成 docs/WIRE_PROTOCOL.md
uv run python scripts/gen_protocol_doc.py --check  # 校验协议文档与代码同源
```

## 相关文档

- [docs/WIRE_PROTOCOL.md](docs/WIRE_PROTOCOL.md) — 通信协议（由脚本自动生成，勿手动编辑）
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — 运维手册（启动 / 停止 / 配置 / 常见错误）
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) — 性能优化方案（A/B/C/D 分级，含落地清单）

