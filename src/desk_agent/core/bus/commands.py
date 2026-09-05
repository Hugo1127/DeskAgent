from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field

from desk_agent.core.session.model import SessionMode, SessionStatus


class PingCommand(BaseModel):
    type: Literal["core.ping"] = "core.ping"
    client: str


class PongResult(BaseModel):
    server_version: str
    uptime_ms: int
    received_at: str  # ISO 8601


# 连接握手：在 TLS 信道内提交令牌，成功后连接进入 AUTHENTICATED 状态
class HelloCommand(BaseModel):
    type: Literal["core.hello"] = "core.hello"
    token: str   # 来自 ~/.desk/run/daemon.json（每次 daemon 启动轮换）
    client: str  # 客户端标识，如 "tui/0.0.1"


class HelloResult(BaseModel):
    protocol_version: int
    server_version: str
    client_id: str


class AgentRunCommand(BaseModel):
    type: Literal["agent.run"] = "agent.run"
    goal: str


class AgentRunResult(BaseModel):
    run_id: str


class EventSubscribeCommand(BaseModel):
    type: Literal["event.subscribe"] = "event.subscribe"
    topics: list[str]          # fnmatch 模式，如 ["step.*", "tool.*"]
    scope: str = "global"      # "global" | "run:<run_id>"
    replay_from_run: str | None = None  # 设置则先从 events.jsonl 回放历史再接实时流


class EventSubscribeResult(BaseModel):
    subscription_id: str
    replayed_count: int = 0


class SessionCreateCommand(BaseModel):
    type: Literal["session.create"] = "session.create"
    mode: SessionMode = "chat"
    title: str = ""


class SessionCreateResult(BaseModel):
    session_id: str
    status: SessionStatus


class SessionSendMessageCommand(BaseModel):
    type: Literal["session.send_message"] = "session.send_message"
    session_id: str
    content: str


class SessionSendMessageResult(BaseModel):
    run_id: str


class SessionGetHistoryCommand(BaseModel):
    type: Literal["session.get_history"] = "session.get_history"
    session_id: str


class SessionGetHistoryResult(BaseModel):
    messages: list[dict[str, Any]]


class SessionCloseCommand(BaseModel):
    type: Literal["session.close"] = "session.close"
    session_id: str


class SessionCloseResult(BaseModel):
    status: SessionStatus


class PermissionPreviewCommand(BaseModel):
    type: Literal["permission.preview"] = "permission.preview"
    tool_use_id: str
    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    session_id: str = ""


class PermissionPreviewResult(BaseModel):
    ok: bool = True
    tool_use_id: str = ""
    # "allow" | "deny" | "ask"（决策链轨迹与真实审批一致）
    decision: str = ""
    tier: str = ""
    matched_rule: str = ""
    reason: str = ""
    preview_text: str = ""
    safe: bool = True


class PermissionRespondCommand(BaseModel):
    type: Literal["permission.respond"] = "permission.respond"
    tool_use_id: str
    # "allow_once" | "always_allow" | "always_allow_global"
    # | "deny_once" | "always_deny" | "always_deny_global"
    decision: str


class PermissionRespondResult(BaseModel):
    ok: bool = True


class SessionCompactCommand(BaseModel):
    type: Literal["session.compact"] = "session.compact"
    session_id: str
    focus: str = ""


class SessionCompactResult(BaseModel):
    summary_tokens: int
    saved_tokens: int


class SessionListCommand(BaseModel):
    type: Literal["session.list"] = "session.list"


class SessionSummary(BaseModel):
    id: str
    mode: str
    title: str
    status: str
    created_at: str
    updated_at: str
    run_count: int


class SessionListResult(BaseModel):
    sessions: list[SessionSummary]


# 根据 type 字段决定命令类型的判别联合
Command = Annotated[
    PingCommand
    | AgentRunCommand
    | EventSubscribeCommand
    | SessionCreateCommand
    | SessionSendMessageCommand
    | SessionGetHistoryCommand
    | SessionCloseCommand
    | PermissionRespondCommand
    | SessionCompactCommand
    | SessionListCommand
    | PermissionPreviewCommand,
    Discriminator("type"),
]
