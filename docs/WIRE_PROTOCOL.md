# 通信协议（Wire Protocol）

> DeskAgent 个人桌面管家 · 由 `scripts/gen_protocol_doc.py` 自动生成。**请勿手动编辑。**

## 传输层（Transport）

- TCP 回环地址 `127.0.0.1:7437`（可通过 `DESK_HOST` / `DESK_PORT` 覆盖）
- 每条消息为一行以 `\n` 结尾的 JSON（NDJSON）
- 命令使用 JSON-RPC 2.0（客户端 → 服务端）；事件使用 `kind=event` 信封（服务端 → 客户端）

## 命令（Commands）

所有命令均以 JSON-RPC 2.0 请求发送，通过 `params` 内的 `type` 字段进行路由。

### PingCommand

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `client` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "core.ping",
      "default": "core.ping",
      "title": "类型",
      "type": "string"
    },
    "client": {
      "title": "客户端标识",
      "type": "string"
    }
  },
  "required": [
    "client"
  ],
  "title": "PingCommand",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `server_version` | `string` | 是 |
| `uptime_ms` | `integer` | 是 |
| `received_at` | `string` | 是 |

```json
{
  "properties": {
    "server_version": {
      "title": "服务端版本",
      "type": "string"
    },
    "uptime_ms": {
      "title": "运行时长（毫秒）",
      "type": "integer"
    },
    "received_at": {
      "title": "接收时间",
      "type": "string"
    }
  },
  "required": [
    "server_version",
    "uptime_ms",
    "received_at"
  ],
  "title": "PongResult",
  "type": "object"
}
```

**示例：**

```json
{
  "jsonrpc": "2.0",
  "id": "u-1",
  "result": {
    "server_version": "0.2.0",
    "uptime_ms": 12,
    "received_at": "2026-05-16T10:00:00.001Z"
  }
}
```

### AgentRunCommand

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `goal` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "agent.run",
      "default": "agent.run",
      "title": "类型",
      "type": "string"
    },
    "goal": {
      "title": "目标",
      "type": "string"
    }
  },
  "required": [
    "goal"
  ],
  "title": "AgentRunCommand",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `run_id` | `string` | 是 |

```json
{
  "properties": {
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    }
  },
  "required": [
    "run_id"
  ],
  "title": "AgentRunResult",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `topics` | `array` | 是 |
| `scope` | `string` | 否 |
| `replay_from_run` | `string | null` | 否 |

```json
{
  "properties": {
    "type": {
      "const": "event.subscribe",
      "default": "event.subscribe",
      "title": "类型",
      "type": "string"
    },
    "topics": {
      "items": {
        "type": "string"
      },
      "title": "订阅主题",
      "type": "array"
    },
    "scope": {
      "default": "global",
      "title": "作用域",
      "type": "string"
    },
    "replay_from_run": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "回放来源运行 ID"
    }
  },
  "required": [
    "topics"
  ],
  "title": "EventSubscribeCommand",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `subscription_id` | `string` | 是 |
| `replayed_count` | `integer` | 否 |

```json
{
  "properties": {
    "subscription_id": {
      "title": "订阅 ID",
      "type": "string"
    },
    "replayed_count": {
      "default": 0,
      "title": "回放事件数",
      "type": "integer"
    }
  },
  "required": [
    "subscription_id"
  ],
  "title": "EventSubscribeResult",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `mode` | `string` | 否 |
| `title` | `string` | 否 |

```json
{
  "properties": {
    "type": {
      "const": "session.create",
      "default": "session.create",
      "title": "类型",
      "type": "string"
    },
    "mode": {
      "default": "chat",
      "enum": [
        "one_shot",
        "chat"
      ],
      "title": "会话模式",
      "type": "string"
    },
    "title": {
      "default": "",
      "title": "标题",
      "type": "string"
    }
  },
  "title": "SessionCreateCommand",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `session_id` | `string` | 是 |
| `status` | `string` | 是 |

```json
{
  "properties": {
    "session_id": {
      "title": "会话 ID",
      "type": "string"
    },
    "status": {
      "enum": [
        "active",
        "waiting_for_input",
        "closed"
      ],
      "title": "状态",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "status"
  ],
  "title": "SessionCreateResult",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `session_id` | `string` | 是 |
| `content` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "session.send_message",
      "default": "session.send_message",
      "title": "类型",
      "type": "string"
    },
    "session_id": {
      "title": "会话 ID",
      "type": "string"
    },
    "content": {
      "title": "内容",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "content"
  ],
  "title": "SessionSendMessageCommand",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `run_id` | `string` | 是 |

```json
{
  "properties": {
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    }
  },
  "required": [
    "run_id"
  ],
  "title": "SessionSendMessageResult",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `session_id` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "session.get_history",
      "default": "session.get_history",
      "title": "类型",
      "type": "string"
    },
    "session_id": {
      "title": "会话 ID",
      "type": "string"
    }
  },
  "required": [
    "session_id"
  ],
  "title": "SessionGetHistoryCommand",
  "type": "object"
}
```

### SessionGetHistoryResult

| 字段 | 类型 | 必填 |
|---|---|---|
| `messages` | `array` | 是 |

```json
{
  "properties": {
    "messages": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "消息列表",
      "type": "array"
    }
  },
  "required": [
    "messages"
  ],
  "title": "SessionGetHistoryResult",
  "type": "object"
}
```

### SessionCloseCommand

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `session_id` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "session.close",
      "default": "session.close",
      "title": "类型",
      "type": "string"
    },
    "session_id": {
      "title": "会话 ID",
      "type": "string"
    }
  },
  "required": [
    "session_id"
  ],
  "title": "SessionCloseCommand",
  "type": "object"
}
```

### SessionCloseResult

| 字段 | 类型 | 必填 |
|---|---|---|
| `status` | `string` | 是 |

```json
{
  "properties": {
    "status": {
      "enum": [
        "active",
        "waiting_for_input",
        "closed"
      ],
      "title": "状态",
      "type": "string"
    }
  },
  "required": [
    "status"
  ],
  "title": "SessionCloseResult",
  "type": "object"
}
```

## 服务端推送（Server Push）

守护进程通过同一 TCP 连接向已订阅的客户端推送事件。

### EventPushEnvelope

| 字段 | 类型 | 必填 |
|---|---|---|
| `kind` | `string` | 否 |
| `event` | `object` | 是 |

```json
{
  "properties": {
    "kind": {
      "const": "event",
      "default": "event",
      "title": "信封类型",
      "type": "string"
    },
    "event": {
      "additionalProperties": true,
      "title": "事件对象",
      "type": "object"
    }
  },
  "required": [
    "event"
  ],
  "title": "EventPushEnvelope",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `listen_addr` | `string` | 是 |
| `version` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "core.started",
      "default": "core.started",
      "title": "类型",
      "type": "string"
    },
    "listen_addr": {
      "title": "监听地址",
      "type": "string"
    },
    "version": {
      "title": "版本",
      "type": "string"
    }
  },
  "required": [
    "listen_addr",
    "version"
  ],
  "title": "CoreStartedEvent",
  "type": "object"
}
```

## 运行事件（Run Events）

写入 `runs/<run_id>/events.jsonl` 并通过 IPC 转发给已订阅客户端的事件。

### RunStartedEvent

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `goal` | `string` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "run.started",
      "default": "run.started",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "goal": {
      "title": "目标",
      "type": "string"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "goal",
    "ts"
  ],
  "title": "RunStartedEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `status` | `string` | 是 |
| `reason` | `string | null` | 否 |
| `steps` | `integer` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "run.finished",
      "default": "run.finished",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "status": {
      "title": "状态",
      "type": "string"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "结束原因"
    },
    "steps": {
      "title": "总步数",
      "type": "integer"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "status",
    "steps",
    "ts"
  ],
  "title": "RunFinishedEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `step` | `integer` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "step.started",
      "default": "step.started",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "step": {
      "title": "步号",
      "type": "integer"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "step",
    "ts"
  ],
  "title": "StepStartedEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `step` | `integer` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "step.finished",
      "default": "step.finished",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "step": {
      "title": "步号",
      "type": "integer"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "step",
    "ts"
  ],
  "title": "StepFinishedEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `tool_use_id` | `string` | 是 |
| `tool_name` | `string` | 是 |
| `params` | `object` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "tool.call_started",
      "default": "tool.call_started",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "tool_use_id": {
      "title": "工具调用 ID",
      "type": "string"
    },
    "tool_name": {
      "title": "工具名称",
      "type": "string"
    },
    "params": {
      "additionalProperties": true,
      "title": "参数",
      "type": "object"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "tool_use_id",
    "tool_name",
    "params",
    "ts"
  ],
  "title": "ToolCallStartedEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `tool_use_id` | `string` | 是 |
| `tool_name` | `string` | 是 |
| `elapsed_ms` | `integer` | 是 |
| `output` | `string` | 否 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "tool.call_finished",
      "default": "tool.call_finished",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "tool_use_id": {
      "title": "工具调用 ID",
      "type": "string"
    },
    "tool_name": {
      "title": "工具名称",
      "type": "string"
    },
    "elapsed_ms": {
      "title": "耗时（毫秒）",
      "type": "integer"
    },
    "output": {
      "default": "",
      "title": "输出内容",
      "type": "string"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "tool_use_id",
    "tool_name",
    "elapsed_ms",
    "ts"
  ],
  "title": "ToolCallFinishedEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `tool_use_id` | `string` | 是 |
| `tool_name` | `string` | 是 |
| `error_class` | `string` | 是 |
| `error_message` | `string` | 是 |
| `elapsed_ms` | `integer` | 是 |
| `attempt` | `integer` | 否 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "tool.call_failed",
      "default": "tool.call_failed",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "tool_use_id": {
      "title": "工具调用 ID",
      "type": "string"
    },
    "tool_name": {
      "title": "工具名称",
      "type": "string"
    },
    "error_class": {
      "title": "错误类别",
      "type": "string"
    },
    "error_message": {
      "title": "错误信息",
      "type": "string"
    },
    "elapsed_ms": {
      "title": "耗时（毫秒）",
      "type": "integer"
    },
    "attempt": {
      "default": 1,
      "title": "尝试次数",
      "type": "integer"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "tool_use_id",
    "tool_name",
    "error_class",
    "error_message",
    "elapsed_ms",
    "ts"
  ],
  "title": "ToolCallFailedEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `model` | `string` | 是 |
| `strategy` | `string` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "llm.model_selected",
      "default": "llm.model_selected",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "model": {
      "title": "模型",
      "type": "string"
    },
    "strategy": {
      "title": "选择策略",
      "type": "string"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "model",
    "strategy",
    "ts"
  ],
  "title": "LlmModelSelectedEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `token` | `string` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "llm.token",
      "default": "llm.token",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "token": {
      "title": "令牌",
      "type": "string"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "token",
    "ts"
  ],
  "title": "LlmTokenEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `input_tokens` | `integer` | 是 |
| `output_tokens` | `integer` | 是 |
| `cache_read_input_tokens` | `integer` | 是 |
| `cache_creation_input_tokens` | `integer` | 是 |
| `context_pct` | `number` | 否 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "llm.usage",
      "default": "llm.usage",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "input_tokens": {
      "title": "输入令牌数",
      "type": "integer"
    },
    "output_tokens": {
      "title": "输出令牌数",
      "type": "integer"
    },
    "cache_read_input_tokens": {
      "title": "缓存读取输入令牌数",
      "type": "integer"
    },
    "cache_creation_input_tokens": {
      "title": "缓存创建输入令牌数",
      "type": "integer"
    },
    "context_pct": {
      "default": 0.0,
      "title": "上下文占用百分比",
      "type": "number"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "ts"
  ],
  "title": "LlmUsageEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `run_id` | `string` | 是 |
| `level` | `string` | 是 |
| `source` | `string` | 是 |
| `message` | `string` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "log.line",
      "default": "log.line",
      "title": "类型",
      "type": "string"
    },
    "run_id": {
      "title": "运行 ID",
      "type": "string"
    },
    "level": {
      "title": "级别",
      "type": "string"
    },
    "source": {
      "title": "来源",
      "type": "string"
    },
    "message": {
      "title": "消息",
      "type": "string"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "run_id",
    "level",
    "source",
    "message",
    "ts"
  ],
  "title": "LogLineEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `session_id` | `string` | 是 |
| `mode` | `string` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "session.created",
      "default": "session.created",
      "title": "类型",
      "type": "string"
    },
    "session_id": {
      "title": "会话 ID",
      "type": "string"
    },
    "mode": {
      "title": "会话模式",
      "type": "string"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "mode",
    "ts"
  ],
  "title": "SessionCreatedEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `session_id` | `string` | 是 |
| `content` | `string` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "session.message_received",
      "default": "session.message_received",
      "title": "类型",
      "type": "string"
    },
    "session_id": {
      "title": "会话 ID",
      "type": "string"
    },
    "content": {
      "title": "内容",
      "type": "string"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "content",
    "ts"
  ],
  "title": "SessionMessageReceivedEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `session_id` | `string` | 是 |
| `last_run_id` | `string` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "session.waiting_for_input",
      "default": "session.waiting_for_input",
      "title": "类型",
      "type": "string"
    },
    "session_id": {
      "title": "会话 ID",
      "type": "string"
    },
    "last_run_id": {
      "title": "最近一次运行 ID",
      "type": "string"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "last_run_id",
    "ts"
  ],
  "title": "SessionWaitingForInputEvent",
  "type": "object"
}
```

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

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `session_id` | `string` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "session.resumed",
      "default": "session.resumed",
      "title": "类型",
      "type": "string"
    },
    "session_id": {
      "title": "会话 ID",
      "type": "string"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "ts"
  ],
  "title": "SessionResumedEvent",
  "type": "object"
}
```

**示例：**

```json
{
  "type": "session.resumed",
  "session_id": "sess-abc123def456",
  "ts": "2026-05-16T10:00:00.001Z"
}
```

### SessionClosedEvent

| 字段 | 类型 | 必填 |
|---|---|---|
| `type` | `string` | 否 |
| `session_id` | `string` | 是 |
| `ts` | `string` | 是 |

```json
{
  "properties": {
    "type": {
      "const": "session.closed",
      "default": "session.closed",
      "title": "类型",
      "type": "string"
    },
    "session_id": {
      "title": "会话 ID",
      "type": "string"
    },
    "ts": {
      "title": "时间戳",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "ts"
  ],
  "title": "SessionClosedEvent",
  "type": "object"
}
```

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
