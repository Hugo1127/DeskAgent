#!/usr/bin/env python3
"""从 desk_agent.core.bus 中的 pydantic 模型生成 WIRE_PROTOCOL.md（中文版协议文档）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from desk_agent.core.bus.commands import (
    AgentRunCommand,
    AgentRunResult,
    EventSubscribeCommand,
    EventSubscribeResult,
    PingCommand,
    PongResult,
    SessionCloseCommand,
    SessionCloseResult,
    SessionCreateCommand,
    SessionCreateResult,
    SessionGetHistoryCommand,
    SessionGetHistoryResult,
    SessionSendMessageCommand,
    SessionSendMessageResult,
)
from desk_agent.core.bus.envelope import EventPushEnvelope
from desk_agent.core.bus.events import (
    CoreStartedEvent,
    LlmModelSelectedEvent,
    LlmTokenEvent,
    LlmUsageEvent,
    LogLineEvent,
    RunFinishedEvent,
    RunStartedEvent,
    SessionClosedEvent,
    SessionCreatedEvent,
    SessionMessageReceivedEvent,
    SessionResumedEvent,
    SessionWaitingForInputEvent,
    StepFinishedEvent,
    StepStartedEvent,
    ToolCallFailedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)

_OUTPUT_PATH = Path(__file__).parent.parent / "docs" / "WIRE_PROTOCOL.md"


# 字段名 → 中文标题：把 pydantic 自动生成的英文 title（如 "Run Id"）翻译为中文
_FIELD_TITLES_ZH: dict[str, str] = {
    "type": "类型",
    "client": "客户端标识",
    "server_version": "服务端版本",
    "uptime_ms": "运行时长（毫秒）",
    "received_at": "接收时间",
    "goal": "目标",
    "run_id": "运行 ID",
    "topics": "订阅主题",
    "scope": "作用域",
    "replay_from_run": "回放来源运行 ID",
    "subscription_id": "订阅 ID",
    "replayed_count": "回放事件数",
    "mode": "会话模式",
    "title": "标题",
    "session_id": "会话 ID",
    "status": "状态",
    "content": "内容",
    "messages": "消息列表",
    "kind": "信封类型",
    "event": "事件对象",
    "listen_addr": "监听地址",
    "version": "版本",
    "ts": "时间戳",
    "reason": "结束原因",
    "steps": "总步数",
    "step": "步号",
    "tool_use_id": "工具调用 ID",
    "tool_name": "工具名称",
    "params": "参数",
    "elapsed_ms": "耗时（毫秒）",
    "output": "输出内容",
    "error_class": "错误类别",
    "error_message": "错误信息",
    "attempt": "尝试次数",
    "model": "模型",
    "strategy": "选择策略",
    "token": "令牌",
    "input_tokens": "输入令牌数",
    "output_tokens": "输出令牌数",
    "cache_read_input_tokens": "缓存读取输入令牌数",
    "cache_creation_input_tokens": "缓存创建输入令牌数",
    "context_pct": "上下文占用百分比",
    "level": "级别",
    "source": "来源",
    "message": "消息",
    "last_run_id": "最近一次运行 ID",
}


# 从 pydantic 模型生成一个带字段表、JSON Schema 和可选示例的 Markdown 小节（中文版）
def _model_section(name: str, model: type, example: dict | None = None) -> str:  # type: ignore[type-arg]
    schema = model.model_json_schema()  # type: ignore[attr-defined]
    props = schema.get("properties", {})
    required: set[str] = set(schema.get("required", []))

    # 把 pydantic 自动生成的英文 title 翻译成中文
    for field_name, field_info in props.items():
        if isinstance(field_info, dict) and field_name in _FIELD_TITLES_ZH:
            field_info["title"] = _FIELD_TITLES_ZH[field_name]

    table = ""
    if props:
        table = "\n| 字段 | 类型 | 必填 |\n|---|---|---|\n"
        for field_name, field_info in props.items():
            ftype = field_info.get("type", "object")
            if "anyOf" in field_info:
                ftype = " | ".join(t.get("type", "?") for t in field_info["anyOf"])
            req = "是" if field_name in required else "否"
            table += f"| `{field_name}` | `{ftype}` | {req} |\n"

    schema_block = f"\n```json\n{json.dumps(schema, indent=2, ensure_ascii=False)}\n```\n"

    example_block = ""
    if example:
        example_block = f"\n**示例：**\n\n```json\n{json.dumps(example, indent=2, ensure_ascii=False)}\n```\n"

    return f"### {name}\n{table}{schema_block}{example_block}"


# 生成完整的 WIRE_PROTOCOL.md 文档字符串
def generate() -> str:
    run_id = "20260516-100000-abc123"
    ts = "2026-05-16T10:00:00.001Z"

    ping_req_example = {
        "jsonrpc": "2.0",
        "id": "u-1",
        "method": "core.ping",
        "params": {"client": "cli/0.0.1"},
    }
    pong_resp_example = {
        "jsonrpc": "2.0",
        "id": "u-1",
        "result": {
            "server_version": "0.2.0",
            "uptime_ms": 12,
            "received_at": ts,
        },
    }
    agent_run_req_example = {
        "jsonrpc": "2.0",
        "id": "u-2",
        "method": "agent.run",
        "params": {"goal": "总结 README.md 的主要章节"},
    }
    agent_run_resp_example = {
        "jsonrpc": "2.0",
        "id": "u-2",
        "result": {"run_id": run_id},
    }
    subscribe_req_example = {
        "jsonrpc": "2.0",
        "id": "u-3",
        "method": "event.subscribe",
        "params": {
            "topics": ["run.*", "step.*", "tool.*", "llm.token"],
            "scope": "global",
            "replay_from_run": None,
        },
    }
    subscribe_resp_example = {
        "jsonrpc": "2.0",
        "id": "u-3",
        "result": {"subscription_id": "sub-abc123", "replayed_count": 0},
    }
    session_id = "sess-abc123def456"
    session_create_req_example = {
        "jsonrpc": "2.0",
        "id": "u-4",
        "method": "session.create",
        "params": {"mode": "chat", "title": ""},
    }
    session_create_resp_example = {
        "jsonrpc": "2.0",
        "id": "u-4",
        "result": {"session_id": session_id, "status": "active"},
    }
    session_send_req_example = {
        "jsonrpc": "2.0",
        "id": "u-5",
        "method": "session.send_message",
        "params": {"session_id": session_id, "content": "总结 README.md"},
    }
    session_send_resp_example = {
        "jsonrpc": "2.0",
        "id": "u-5",
        "result": {"run_id": run_id},
    }
    event_push_example = {
        "kind": "event",
        "event": {
            "type": "step.started",
            "run_id": run_id,
            "step": 1,
            "ts": ts,
        },
    }

    sections = [
        "# 通信协议（Wire Protocol）\n\n",
        "> DeskAgent 个人桌面管家 · 由 `scripts/gen_protocol_doc.py` 自动生成。**请勿手动编辑。**\n\n",
        "## 传输层（Transport）\n\n",
        "- TCP 回环地址 `127.0.0.1:7437`（可通过 `DESK_HOST` / `DESK_PORT` 覆盖）\n",
        "- 每条消息为一行以 `\\n` 结尾的 JSON（NDJSON）\n",
        "- 命令使用 JSON-RPC 2.0（客户端 → 服务端）；事件使用 `kind=event` 信封（服务端 → 客户端）\n\n",
        "## 命令（Commands）\n\n",
        "所有命令均以 JSON-RPC 2.0 请求发送，通过 `params` 内的 `type` 字段进行路由。\n\n",
        _model_section("PingCommand", PingCommand, ping_req_example),
        "\n",
        _model_section("PongResult", PongResult, pong_resp_example),
        "\n",
        _model_section("AgentRunCommand", AgentRunCommand, agent_run_req_example),
        "\n",
        _model_section("AgentRunResult", AgentRunResult, agent_run_resp_example),
        "\n",
        _model_section("EventSubscribeCommand", EventSubscribeCommand, subscribe_req_example),
        "\n",
        _model_section("EventSubscribeResult", EventSubscribeResult, subscribe_resp_example),
        "\n",
        _model_section("SessionCreateCommand", SessionCreateCommand, session_create_req_example),
        "\n",
        _model_section("SessionCreateResult", SessionCreateResult, session_create_resp_example),
        "\n",
        _model_section("SessionSendMessageCommand", SessionSendMessageCommand, session_send_req_example),
        "\n",
        _model_section("SessionSendMessageResult", SessionSendMessageResult, session_send_resp_example),
        "\n",
        _model_section("SessionGetHistoryCommand", SessionGetHistoryCommand),
        "\n",
        _model_section("SessionGetHistoryResult", SessionGetHistoryResult),
        "\n",
        _model_section("SessionCloseCommand", SessionCloseCommand),
        "\n",
        _model_section("SessionCloseResult", SessionCloseResult),
        "\n## 服务端推送（Server Push）\n\n",
        "守护进程通过同一 TCP 连接向已订阅的客户端推送事件。\n\n",
        _model_section("EventPushEnvelope", EventPushEnvelope, event_push_example),
        "\n## IPC 事件（IPC Events）\n\n",
        "通过 IPC 套接字发送的事件（守护进程 → 客户端）。\n\n",
        _model_section("CoreStartedEvent", CoreStartedEvent),
        "\n## 运行事件（Run Events）\n\n",
        "写入 `runs/<run_id>/events.jsonl` 并通过 IPC 转发给已订阅客户端的事件。\n\n",
        _model_section("RunStartedEvent", RunStartedEvent,
            {"type": "run.started", "run_id": run_id, "goal": "总结 README.md", "ts": ts}),
        "\n",
        _model_section("RunFinishedEvent", RunFinishedEvent, {
            "type": "run.finished", "run_id": run_id,
            "status": "success", "reason": None, "steps": 2, "ts": ts}),
        "\n",
        _model_section("StepStartedEvent", StepStartedEvent,
            {"type": "step.started", "run_id": run_id, "step": 1, "ts": ts}),
        "\n",
        _model_section("StepFinishedEvent", StepFinishedEvent,
            {"type": "step.finished", "run_id": run_id, "step": 1, "ts": ts}),
        "\n",
        _model_section("ToolCallStartedEvent", ToolCallStartedEvent,
            {"type": "tool.call_started", "run_id": run_id, "tool_use_id": "toolu_01",
             "tool_name": "read_file", "params": {"path": "README.md"}, "ts": ts}),
        "\n",
        _model_section("ToolCallFinishedEvent", ToolCallFinishedEvent,
            {"type": "tool.call_finished", "run_id": run_id, "tool_use_id": "toolu_01",
             "tool_name": "read_file", "elapsed_ms": 3, "ts": ts}),
        "\n",
        _model_section("ToolCallFailedEvent", ToolCallFailedEvent,
            {"type": "tool.call_failed", "run_id": run_id, "tool_use_id": "toolu_02",
             "tool_name": "read_file", "error_class": "runtime_error",
             "error_message": "file not found", "elapsed_ms": 1, "attempt": 1, "ts": ts}),
        "\n",
        _model_section("LlmModelSelectedEvent", LlmModelSelectedEvent,
            {"type": "llm.model_selected", "run_id": run_id,
             "model": "claude-sonnet-4-6", "strategy": "static", "ts": ts}),
        "\n",
        _model_section("LlmTokenEvent", LlmTokenEvent,
            {"type": "llm.token", "run_id": run_id, "token": "The ", "ts": ts}),
        "\n",
        _model_section("LlmUsageEvent", LlmUsageEvent,
            {"type": "llm.usage", "run_id": run_id, "input_tokens": 512, "output_tokens": 48,
             "cache_read_input_tokens": 490, "cache_creation_input_tokens": 0, "ts": ts}),
        "\n",
        _model_section("LogLineEvent", LogLineEvent,
            {"type": "log.line", "run_id": run_id, "level": "INFO",
             "source": "desk_agent.core.loop", "message": "step 1 started", "ts": ts}),
        "\n## 会话事件（Session Events）\n\n",
        _model_section("SessionCreatedEvent", SessionCreatedEvent,
            {"type": "session.created", "session_id": session_id, "mode": "chat", "ts": ts}),
        "\n",
        _model_section("SessionMessageReceivedEvent", SessionMessageReceivedEvent,
            {"type": "session.message_received", "session_id": session_id,
             "content": "总结 README.md", "ts": ts}),
        "\n",
        _model_section("SessionWaitingForInputEvent", SessionWaitingForInputEvent,
            {"type": "session.waiting_for_input", "session_id": session_id,
             "last_run_id": run_id, "ts": ts}),
        "\n",
        _model_section("SessionResumedEvent", SessionResumedEvent,
            {"type": "session.resumed", "session_id": session_id, "ts": ts}),
        "\n",
        _model_section("SessionClosedEvent", SessionClosedEvent,
            {"type": "session.closed", "session_id": session_id, "ts": ts}),
        "\n## 错误码（Error Codes）\n\n",
        "| 代码 | 名称 | 含义 |\n",
        "|------|------|------|\n",
        "| -32700 | 解析错误 | 收到的 JSON 无效 |\n",
        "| -32600 | 无效请求 | 缺少 JSON-RPC 必填字段 |\n",
        "| -32601 | 方法不存在 | 未知的方法 |\n",
        "| -32602 | 参数无效 | 参数校验失败 |\n",
        "| -32603 | 内部错误 | 处理器抛出未处理异常 |\n",
        "| -32000 | 应用错误 | 例如已有另一个运行正在进行中 |\n",
    ]
    return "".join(sections)


# 解析命令行参数，写出或校验 WIRE_PROTOCOL.md
def main() -> None:
    parser = argparse.ArgumentParser(description="生成中文版 WIRE_PROTOCOL.md")
    parser.add_argument("--check", action="store_true", help="校验文件与生成的输出是否一致")
    parser.add_argument("--output", default=str(_OUTPUT_PATH))
    args = parser.parse_args()

    content = generate()

    if args.check:
        output_path = Path(args.output)
        if not output_path.exists():
            print(f"错误：未找到 {output_path} —— 请先运行：make docs", file=sys.stderr)
            sys.exit(1)
        if output_path.read_text(encoding="utf-8") != content:
            print(f"错误：{output_path} 与代码不同步 —— 请运行：make docs", file=sys.stderr)
            sys.exit(1)
        print(f"OK：{output_path} 已是最新。")
    else:
        output_path = Path(args.output)
        output_path.write_text(content, encoding="utf-8")
        print(f"已生成 {output_path}")


if __name__ == "__main__":
    main()
