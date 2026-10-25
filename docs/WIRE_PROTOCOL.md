# 通信协议（Wire Protocol）

> DeskAgent 个人桌面管家 · 由 `scripts/gen_protocol_doc.py` 自动生成。**请勿手动编辑。**

## 传输层（Transport）

- TCP 回环地址 `127.0.0.1:9986`（可通过 `DESK_HOST` / `DESK_PORT` 覆盖）
- **传输安全**：默认启用 TLS（自签证书 + 证书固定，见 docs/COMMUNICATION_SECURITY.md）
- 每条消息为一行以 `\n` 结尾的 JSON（NDJSON）
- 每条请求携带 `seq`（每连接单调递增，防重放）与 `ts`（epoch 秒时间戳，防过期重放）
- 连接后必须先 `core.hello` 提交令牌完成认证，未认证连接只能调用 `core.hello`
- 命令使用 JSON-RPC 2.0（客户端 → 服务端）；事件使用 `kind=event` 信封（服务端 → 客户端）

## 命令（Commands）

所有命令均以 JSON-RPC 2.0 请求发送，通过 `params` 内的 `type` 字段进行路由。

### HelloCommand

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `core.hello` | 否 | 类型 |
| `token` | `string` | 是 | 令牌 |
| `client` | `string` | 是 | 客户端标识 |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-0",
  "method": "core.hello",
  "params": {
    "token": "<来自 ~/.desk/run/daemon.json>",
    "client": "tui/0.0.1"
  },
  "seq": 1,
  "ts": 1786782000
}
```

### HelloResult

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `protocol_version` | `integer` | 是 | Protocol Version |
| `server_version` | `string` | 是 | 服务端版本 |
| `client_id` | `string` | 是 | Client Id |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-0",
  "result": {
    "protocol_version": 1,
    "server_version": "0.0.1",
    "client_id": "3f2a9c1e8b7d4a6f"
  }
}
```

### PingCommand

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `core.ping` | 否 | 类型 |
| `client` | `string` | 是 | 客户端标识 |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-1",
  "method": "core.ping",
  "params": {
    "client": "cli/0.0.1"
  }
}
```

### PongResult

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `server_version` | `string` | 是 | 服务端版本 |
| `uptime_ms` | `integer` | 是 | 运行时长（毫秒） |
| `received_at` | `string` | 是 | 接收时间 |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-1",
  "result": {
    "server_version": "0.0.1",
    "uptime_ms": 12,
    "received_at": "2026-05-16T10:00:00.001Z"
  }
}
```

### AgentRunCommand

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `agent.run` | 否 | 类型 |
| `goal` | `string` | 是 | 目标 |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-2",
  "method": "agent.run",
  "params": {
    "goal": "总结 README.md 的主要章节"
  }
}
```

### AgentRunResult

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `run_id` | `string` | 是 | 运行 ID |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-2",
  "result": {
    "run_id": "20260516-100000-abc123"
  }
}
```

### EventSubscribeCommand

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `event.subscribe` | 否 | 类型 |
| `topics` | `array` | 是 | 订阅主题 |
| `scope` | `string`（默认: "global"） | 否 | 作用域 |
| `replay_from_run` | `string | null` | 否 | 回放来源运行 ID |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-3",
  "method": "event.subscribe",
  "params": {
    "topics": [
      "run.*",
      "step.*",
      "tool.*",
      "llm.token"
    ],
    "scope": "global",
    "replay_from_run": null
  }
}
```

### EventSubscribeResult

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `subscription_id` | `string` | 是 | 订阅 ID |
| `replayed_count` | `integer`（默认: 0） | 否 | 回放事件数 |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-3",
  "result": {
    "subscription_id": "sub-abc123",
    "replayed_count": 0
  }
}
```

### SessionCreateCommand

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `session.create` | 否 | 类型 |
| `mode` | `string`（枚举: one_shot / chat；默认: "chat"） | 否 | 会话模式 |
| `title` | `string`（默认: ""） | 否 | 标题 |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-4",
  "method": "session.create",
  "params": {
    "mode": "chat",
    "title": ""
  }
}
```

### SessionCreateResult

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `status` | `string`（枚举: active / waiting_for_input / closed） | 是 | 状态 |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-4",
  "result": {
    "session_id": "sess-abc123def456",
    "status": "active"
  }
}
```

### SessionSendMessageCommand

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `session.send_message` | 否 | 类型 |
| `session_id` | `string` | 是 | 会话 ID |
| `content` | `string` | 是 | 内容 |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-5",
  "method": "session.send_message",
  "params": {
    "session_id": "sess-abc123def456",
    "content": "总结 README.md"
  }
}
```

### SessionSendMessageResult

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `run_id` | `string` | 是 | 运行 ID |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-5",
  "result": {
    "run_id": "20260516-100000-abc123"
  }
}
```

### SessionGetHistoryCommand

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `session.get_history` | 否 | 类型 |
| `session_id` | `string` | 是 | 会话 ID |

### SessionGetHistoryResult

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `messages` | `array` | 是 | 消息列表 |

### SessionCloseCommand

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `session.close` | 否 | 类型 |
| `session_id` | `string` | 是 | 会话 ID |

### SessionCloseResult

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | `string`（枚举: active / waiting_for_input / closed） | 是 | 状态 |

### SessionListCommand

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `session.list` | 否 | 类型 |

### SessionListResult

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessions` | `array` | 是 | 会话摘要列表 |

### SessionSummary

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | 会话 ID |
| `mode` | `string` | 是 | 会话模式 |
| `title` | `string` | 是 | 标题 |
| `status` | `string` | 是 | 状态 |
| `created_at` | `string` | 是 | 创建时间 |
| `updated_at` | `string` | 是 | 更新时间 |
| `run_count` | `integer` | 是 | 运行次数 |

### PermissionPreviewCommand

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `permission.preview` | 否 | 类型 |
| `tool_use_id` | `string` | 是 | 工具调用 ID |
| `tool_name` | `string` | 是 | 工具名称 |
| `params` | `object` | 否 | 参数 |
| `session_id` | `string`（默认: ""） | 否 | 会话 ID |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-8",
  "method": "permission.preview",
  "params": {
    "tool_use_id": "toolu_01",
    "tool_name": "bash",
    "params": {
      "command": "rm -rf build/",
      "timeout": 30
    },
    "session_id": "sess-abc123def456"
  }
}
```

### PermissionPreviewResult

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | `boolean`（默认: true） | 否 | Ok |
| `tool_use_id` | `string`（默认: ""） | 否 | 工具调用 ID |
| `decision` | `string`（默认: ""） | 否 | 决策结果 |
| `tier` | `string`（默认: ""） | 否 | 命中决策层级 |
| `forced` | `boolean`（默认: false） | 否 | Forced |
| `matched_rule` | `string`（默认: ""） | 否 | 命中规则 |
| `reason` | `string`（默认: ""） | 否 | 原因说明 |
| `preview_text` | `string`（默认: ""） | 否 | 预览文本 |
| `safe` | `boolean`（默认: true） | 否 | 是否安全 |

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-8",
  "result": {
    "ok": true,
    "tool_use_id": "toolu_01",
    "decision": "ask",
    "tier": "tier2_outside_cwd",
    "matched_rule": "outside-cwd heuristic",
    "reason": "bash 命令疑似操作工作目录之外路径，强制询问，不可被缓存绕过",
    "preview_text": "命令（不执行，仅静态预览）:...",
    "safe": true
  }
}
```

## 服务端推送（Server Push）

守护进程通过同一 TCP 连接向已订阅的客户端推送事件。

### EventPushEnvelope

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | `event` | 否 | 信封类型 |
| `event` | `object` | 是 | 事件对象 |

**示例：**

```json
{
  "kind": "event",
  "event": {
    "type": "step.started",
    "run_id": "20260516-100000-abc123",
    "step": 1,
    "ts": "2026-05-16T10:00:00.001Z"
  }
}
```

## IPC 事件（IPC Events）

通过 IPC 套接字发送的事件（守护进程 → 客户端）。

### CoreStartedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `core.started` | 否 | 类型 |
| `listen_addr` | `string` | 是 | 监听地址 |
| `version` | `string` | 是 | 版本 |

## 运行事件（Run Events）

写入 `runs/<run_id>/events.jsonl` 并通过 IPC 转发给已订阅客户端的事件。

### RunStartedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `run.started` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `goal` | `string` | 是 | 目标 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "run.started",
  "run_id": "20260516-100000-abc123",
  "goal": "总结 README.md",
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### RunFinishedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `run.finished` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `status` | `string` | 是 | 状态 |
| `reason` | `string | null` | 否 | 原因说明 |
| `steps` | `integer` | 是 | 总步数 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "run.finished",
  "run_id": "20260516-100000-abc123",
  "status": "success",
  "reason": null,
  "steps": 2,
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### StepStartedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `step.started` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `step` | `integer` | 是 | 步号 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "step.started",
  "run_id": "20260516-100000-abc123",
  "step": 1,
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### StepFinishedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `step.finished` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `step` | `integer` | 是 | 步号 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "step.finished",
  "run_id": "20260516-100000-abc123",
  "step": 1,
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### ToolCallStartedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `tool.call_started` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `tool_use_id` | `string` | 是 | 工具调用 ID |
| `tool_name` | `string` | 是 | 工具名称 |
| `params` | `object` | 是 | 参数 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "tool.call_started",
  "run_id": "20260516-100000-abc123",
  "tool_use_id": "toolu_01",
  "tool_name": "read_file",
  "params": {
    "path": "README.md"
  },
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### ToolCallFinishedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `tool.call_finished` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `tool_use_id` | `string` | 是 | 工具调用 ID |
| `tool_name` | `string` | 是 | 工具名称 |
| `elapsed_ms` | `integer` | 是 | 耗时（毫秒） |
| `output` | `string`（默认: ""） | 否 | 输出内容 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "tool.call_finished",
  "run_id": "20260516-100000-abc123",
  "tool_use_id": "toolu_01",
  "tool_name": "read_file",
  "elapsed_ms": 3,
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### ToolCallFailedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `tool.call_failed` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `tool_use_id` | `string` | 是 | 工具调用 ID |
| `tool_name` | `string` | 是 | 工具名称 |
| `error_class` | `string` | 是 | 错误类别 |
| `error_message` | `string` | 是 | 错误信息 |
| `elapsed_ms` | `integer` | 是 | 耗时（毫秒） |
| `attempt` | `integer`（默认: 1） | 否 | 尝试次数 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "tool.call_failed",
  "run_id": "20260516-100000-abc123",
  "tool_use_id": "toolu_02",
  "tool_name": "read_file",
  "error_class": "runtime_error",
  "error_message": "file not found",
  "elapsed_ms": 1,
  "attempt": 1,
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### LlmModelSelectedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `llm.model_selected` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `model` | `string` | 是 | 模型 |
| `strategy` | `string` | 是 | 选择策略 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "llm.model_selected",
  "run_id": "20260516-100000-abc123",
  "model": "claude-sonnet-4-6",
  "strategy": "static",
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### LlmTokenEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `llm.token` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `token` | `string` | 是 | 令牌 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "llm.token",
  "run_id": "20260516-100000-abc123",
  "token": "The ",
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### LlmUsageEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `llm.usage` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `input_tokens` | `integer` | 是 | 输入令牌数 |
| `output_tokens` | `integer` | 是 | 输出令牌数 |
| `cache_read_input_tokens` | `integer` | 是 | 缓存读取输入令牌数 |
| `cache_creation_input_tokens` | `integer` | 是 | 缓存创建输入令牌数 |
| `context_pct` | `number`（默认: 0.0） | 否 | 上下文占用百分比 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "llm.usage",
  "run_id": "20260516-100000-abc123",
  "input_tokens": 512,
  "output_tokens": 48,
  "cache_read_input_tokens": 490,
  "cache_creation_input_tokens": 0,
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### LogLineEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `log.line` | 否 | 类型 |
| `run_id` | `string` | 是 | 运行 ID |
| `level` | `string` | 是 | 级别 |
| `source` | `string` | 是 | 来源 |
| `message` | `string` | 是 | 消息 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "log.line",
  "run_id": "20260516-100000-abc123",
  "level": "INFO",
  "source": "desk_agent.core.loop",
  "message": "step 1 started",
  "ts": "2026-05-16T10:00:00.001Z"
}
```

## 会话事件（Session Events）

### SessionCreatedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `session.created` | 否 | 类型 |
| `session_id` | `string` | 是 | 会话 ID |
| `mode` | `string` | 是 | 会话模式 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "session.created",
  "session_id": "sess-abc123def456",
  "mode": "chat",
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### SessionMessageReceivedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `session.message_received` | 否 | 类型 |
| `session_id` | `string` | 是 | 会话 ID |
| `content` | `string` | 是 | 内容 |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "session.message_received",
  "session_id": "sess-abc123def456",
  "content": "总结 README.md",
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### SessionWaitingForInputEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `session.waiting_for_input` | 否 | 类型 |
| `session_id` | `string` | 是 | 会话 ID |
| `last_run_id` | `string` | 是 | 最近一次运行 ID |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "session.waiting_for_input",
  "session_id": "sess-abc123def456",
  "last_run_id": "20260516-100000-abc123",
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### SessionResumedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `session.resumed` | 否 | 类型 |
| `session_id` | `string` | 是 | 会话 ID |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "session.resumed",
  "session_id": "sess-abc123def456",
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### SessionClosedEvent

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `session.closed` | 否 | 类型 |
| `session_id` | `string` | 是 | 会话 ID |
| `ts` | `string` | 是 | 时间戳 |

**示例：**

```json
{
  "type": "session.closed",
  "session_id": "sess-abc123def456",
  "ts": "2026-05-16T10:00:00.001Z"
}
```

## 错误码（Error Codes）

| 代码 | 名称 | 含义 |
|------|------|------|
| -32700 | 解析错误 | 收到的 JSON 无效 |
| -32600 | 无效请求 | 缺少 JSON-RPC 必填字段 |
| -32601 | 方法不存在 | 未知的方法 |
| -32602 | 参数无效 | 参数校验失败 |
| -32603 | 内部错误 | 处理器抛出未处理异常 |
| -32000 | 应用错误 | 例如已有另一个运行正在进行中 |
| -32001 | 未认证 | 未认证连接或令牌错误 |
| -32002 | 无权操作 session | 未持有该 session |
| -32003 | 超出速率限制 | 触发速率限制 |
| -32004 | 令牌失效 | daemon 重启后旧连接 |
| -32005 | 报文过期/重放 | seq 重复或时间戳超出窗口 |
