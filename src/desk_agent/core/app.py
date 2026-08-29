from __future__ import annotations

import asyncio
import datetime
import fnmatch
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import UTC
from pathlib import Path
from typing import Any

from pydantic import BaseModel

import desk_agent
from desk_agent.core.bus.commands import (
    AgentRunCommand,
    AgentRunResult,
    EventSubscribeCommand,
    EventSubscribeResult,
    PermissionRespondCommand,
    PermissionRespondResult,
    PongResult,
    SessionCloseCommand,
    SessionCloseResult,
    SessionCompactCommand,
    SessionCompactResult,
    SessionCreateCommand,
    SessionCreateResult,
    SessionGetHistoryCommand,
    SessionGetHistoryResult,
    SessionListCommand,
    SessionListResult,
    SessionSendMessageCommand,
    SessionSendMessageResult,
    SessionSummary,
)
from desk_agent.core.bus.envelope import EventPushEnvelope
from desk_agent.core.config import DeskConfig, get_config
from desk_agent.core.events.bus import EventBus
from desk_agent.core.llm.provider import AnthropicProvider
from desk_agent.core.logging_setup import setup_logging
from desk_agent.core.mcp.server import McpServerManager
from desk_agent.core.permissions.manager import PermissionManager
from desk_agent.core.permissions.storage import load_policy_file
from desk_agent.core.runner import AgentRunner
from desk_agent.core.runs import events_file, new_run_id
from desk_agent.core.session import SessionManager, SessionStore
from desk_agent.core.trace.record import TraceRecord
from desk_agent.core.trace.writer import TraceWriter
from desk_agent.core.transport import runfile, tls
from desk_agent.core.transport.ipc_broadcaster import IpcEventBroadcaster
from desk_agent.core.transport.socket_server import SocketServer, get_connection_writer

logger = logging.getLogger(__name__)


# 关闭流程的超时上限：超过则强制退出，防止 asyncio/Windows Proactor 收尾竞态导致进程永不退出
# 正常关闭各步均有限时，最坏路径（多 MCP 客户端 + 运行中任务）约 12s，故取 15s 留余量
_SHUTDOWN_WATCHDOG_S = 15.0


def _now() -> str:
    return datetime.datetime.now(UTC).isoformat()


def _watchdog_force_exit() -> None:
    logger.error(
        "shutdown watchdog: shutdown exceeded %.0fs, forcing exit",
        _SHUTDOWN_WATCHDOG_S,
    )
    try:
        import faulthandler
        faulthandler.dump_traceback()
    except Exception:
        pass
    os._exit(0)


class CoreApp:
    def __init__(self) -> None:
        self._start_time = time.monotonic()
        self._bus = EventBus()
        self._broadcaster: IpcEventBroadcaster | None = None
        self._trace: TraceWriter | None = None
        self._config: DeskConfig | None = None
        self._running_runs: set[asyncio.Task[Any]] = set()
        self._sessions: SessionManager | None = None
        self._permission_manager: PermissionManager | None = None
        self._mcp_manager: McpServerManager | None = None

    # 处理 core.ping 请求，返回服务版本、运行时长和接收时间
    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        client = params.get("client", "unknown")
        logger.debug("ping from %s", client)
        return PongResult(
            server_version=desk_agent.__version__,
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    # 将 EventBus 事件写入 trace（作为 EventBus 订阅者）
    async def _trace_event_handler(self, event: BaseModel) -> None:
        assert self._trace is not None
        event_dict = event.model_dump()
        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="CORE",
                layer="event",
                kind="event",
                run_id=event_dict.get("run_id"),
                data=event_dict,
            )
        )

    # 启动一次 agent run：异步创建 AgentRunner 并立即返回 run_id
    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        assert self._sessions is not None
        cmd = AgentRunCommand.model_validate(params)
        session = await self._sessions.create(mode="one_shot", title=cmd.goal[:40])
        run_id = new_run_id()
        run_task = asyncio.create_task(
            self._sessions.send_message(session.id, cmd.goal, run_id=run_id)
        )
        self._running_runs.add(run_task)
        run_task.add_done_callback(self._running_runs.discard)
        return AgentRunResult(run_id=run_id)

    # 创建 chat 或 one_shot session，并返回 session_id
    async def _session_create_handler(self, params: dict[str, Any]) -> SessionCreateResult:
        assert self._sessions is not None
        cmd = SessionCreateCommand.model_validate(params)
        session = await self._sessions.create(mode=cmd.mode, title=cmd.title)
        return SessionCreateResult(session_id=session.id, status=session.status)

    # 向 session 发送一条用户消息并同步等待对应 run 完成
    async def _session_send_handler(self, params: dict[str, Any]) -> SessionSendMessageResult:
        assert self._sessions is not None
        cmd = SessionSendMessageCommand.model_validate(params)
        run_id = await self._sessions.send_message(cmd.session_id, cmd.content)
        return SessionSendMessageResult(run_id=run_id)

    # 返回 session 的完整 Anthropic messages 历史
    async def _session_history_handler(self, params: dict[str, Any]) -> SessionGetHistoryResult:
        assert self._sessions is not None
        cmd = SessionGetHistoryCommand.model_validate(params)
        messages = await self._sessions.get_history(cmd.session_id)
        return SessionGetHistoryResult(messages=messages)

    # 返回磁盘上所有会话摘要（按 updated_at 倒序），供 Web 仪表盘/侧栏展示
    async def _session_list_handler(self, params: dict[str, Any]) -> SessionListResult:
        assert self._sessions is not None
        SessionListCommand.model_validate(params)
        summaries = [
            SessionSummary(
                id=s.id,
                mode=s.mode,
                title=s.title,
                status=s.status,
                created_at=s.created_at,
                updated_at=s.updated_at,
                run_count=len(s.run_ids),
            )
            for s in self._sessions.list_sessions()
        ]
        return SessionListResult(sessions=summaries)

    # 接收客户端权限审批响应，resolve 对应挂起的 Future
    async def _permission_respond_handler(self, params: dict[str, Any]) -> PermissionRespondResult:
        cmd = PermissionRespondCommand.model_validate(params)
        logger.info(
            "permission.respond received tool_use_id=%s decision=%s",
            cmd.tool_use_id, cmd.decision,
        )
        if self._permission_manager is None:
            logger.error("permission.respond: PermissionManager not initialized")
            return PermissionRespondResult()
        self._permission_manager.respond(cmd.tool_use_id, cmd.decision)
        return PermissionRespondResult()

    # 手动压缩 session thread，将摘要持久化写入 thread.jsonl
    async def _session_compact_handler(self, params: dict[str, Any]) -> SessionCompactResult:
        assert self._sessions is not None
        cmd = SessionCompactCommand.model_validate(params)
        result = await self._sessions.compact(cmd.session_id, cmd.focus)
        return result  # type: ignore[no-any-return]

    # 关闭 session 并返回 closed 状态
    async def _session_close_handler(self, params: dict[str, Any]) -> SessionCloseResult:
        assert self._sessions is not None
        cmd = SessionCloseCommand.model_validate(params)
        await self._sessions.close(cmd.session_id)
        return SessionCloseResult(status="closed")

    # 注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流
    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        cmd = EventSubscribeCommand.model_validate(params)
        writer = get_connection_writer()

        replayed_count = 0
        if cmd.replay_from_run is not None:
            replayed_count = await self._replay_events(
                cmd.replay_from_run, writer, cmd.topics
            )

        assert self._broadcaster is not None
        sub_id = self._broadcaster.subscribe(writer, cmd.topics, cmd.scope)
        return EventSubscribeResult(subscription_id=sub_id, replayed_count=replayed_count)

    # 从 events.jsonl 向 writer 回放匹配 topic 的历史事件，返回已回放条数
    async def _replay_events(
        self,
        run_id: str,
        writer: asyncio.StreamWriter,
        topics: list[str],
    ) -> int:
        path = events_file(run_id)
        if not path.exists():
            for candidate in Path("~/.desk/sessions").expanduser().glob(
                f"*/runs/{run_id}/events.jsonl"
            ):
                path = candidate
                break
        if not path.exists():
            return 0

        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type: str = event.get("type", "")
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):
                continue
            envelope = EventPushEnvelope(event=event)
            writer.write(envelope.model_dump_json().encode() + b"\n")
            count += 1

        if count:
            await writer.drain()
        return count

    # 启动守护进程：加载配置、初始化日志、启动 trace、启动 TCP 服务器，并等待退出信号
    async def run(self) -> None:
        self._start_time = time.monotonic()
        self._config = get_config()
        setup_logging(self._config)

        if self._config.trace.enabled:
            trace_path = Path(self._config.trace.file).expanduser()
            self._trace = TraceWriter(trace_path)
            await self._trace.start()
            self._bus.subscribe(self._trace_event_handler)

        policy_file = Path("~/.desk/policy.toml").expanduser()
        self._permission_manager = PermissionManager(
            policy_file=policy_file,
            timeout_s=self._config.permission.timeout_s,
        )
        logger.info(
            "permission manager: timeout_s=%.1f  persistent=%d entries",
            self._config.permission.timeout_s,
            len(load_policy_file(policy_file)),
        )

        self._broadcaster = IpcEventBroadcaster(trace=self._trace)
        self._bus.subscribe(self._broadcaster.handle)
        sessions_root = Path("~/.desk/sessions").expanduser()
        store = SessionStore(sessions_root)
        assert self._config is not None
        compact_provider = AnthropicProvider(self._config.llm.default_model)

        self._mcp_manager = McpServerManager()
        if self._config.mcp.servers:
            logger.info("mcp: starting %d server(s)", len(self._config.mcp.servers))
            await self._mcp_manager.start_all(self._config.mcp.servers)

        self._sessions = SessionManager(
            store,
            runner_factory=lambda: AgentRunner(
                self._config,  # type: ignore[arg-type]
                bus=self._bus,
                trace=self._trace,
                permission_manager=self._permission_manager,
                mcp_manager=self._mcp_manager,
            ),
            bus=self._bus,
            provider=compact_provider,
        )

        # IPC 通信安全：每实例随机令牌 + TLS 自签证书（见 docs/COMMUNICATION_SECURITY.md）
        token = runfile.new_token()
        ssl_context = None
        cert_sha256 = None
        if self._config.tls:
            cert_sha256 = tls.generate_self_signed(
                hosts=[self._config.host, "127.0.0.1", "localhost"],
                key_path=runfile.key_file_path(),
                cert_path=runfile.cert_file_path(),
            )
            ssl_context = tls.server_context(
                runfile.cert_file_path(), runfile.key_file_path()
            )

        server = SocketServer(
            self._config.host,
            self._config.port,
            self._broadcaster,
            trace=self._trace,
            token=token if self._config.auth else None,
            ssl_context=ssl_context,
        )
        server.register("core.ping", self._ping_handler)
        server.register("agent.run", self._agent_run_handler)
        server.register("event.subscribe", self._subscribe_handler)
        server.register("session.create", self._session_create_handler)
        server.register("session.send_message", self._session_send_handler)
        server.register("session.get_history", self._session_history_handler)
        server.register("session.list", self._session_list_handler)
        server.register("session.close", self._session_close_handler)
        server.register("permission.respond", self._permission_respond_handler)
        server.register("session.compact", self._session_compact_handler)

        addr = await server.start()
        # 服务就绪后写 run 文件（token/port/pid/证书指纹），客户端据此连接
        port = int(addr.rsplit(":", 1)[1])
        runfile.write_daemon_file(
            runfile.DaemonInfo(
                token=token,
                port=port,
                pid=os.getpid(),
                started_at=_now(),
                version=desk_agent.__version__,
                cert_sha256=cert_sha256,
            )
        )
        logger.info("desk-core %s listening addr=%s auth=%s tls=%s",
                    desk_agent.__version__, addr, self._config.auth, self._config.tls)
        logger.info("config: %s", self._config)

        loop = asyncio.get_running_loop()
        shutdown = asyncio.Event()
        # 注册退出信号：Windows 的 loop 不支持 add_signal_handler，用 signal.signal + call_soon_threadsafe 替代  # noqa: E501
        if sys.platform == "win32":
            signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(shutdown.set))
            signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(shutdown.set))
        else:
            loop.add_signal_handler(signal.SIGINT, shutdown.set)
            loop.add_signal_handler(signal.SIGTERM, shutdown.set)

        await shutdown.wait()

        logger.info("shutting down")
        # 看门狗：若关闭流程（含 asyncio.run 收尾）卡住，超时后强制退出，保证 daemon 必定退出。
        # 必须用 daemon 线程——非守护线程会在解释器退出时被 join，反而阻塞正常退出。
        # 正常退出远快于此限时，此时进程已结束，计时器随之消失，不会误杀。
        watchdog = threading.Timer(_SHUTDOWN_WATCHDOG_S, _watchdog_force_exit)
        watchdog.daemon = True
        watchdog.start()

        for run_task in list(self._running_runs):
            run_task.cancel()
        if self._running_runs:
            await asyncio.wait_for(
                asyncio.gather(*self._running_runs, return_exceptions=True),
                timeout=5.0,
            )
        if self._mcp_manager is not None:
            await self._mcp_manager.stop_all()
        await server.stop()
        # 清理 run 文件（含令牌与 PID 信息），客户端下次连接需重新读取
        runfile.remove_daemon_file()
        runfile.remove_legacy_pid_file()
        if self._trace is not None:
            await self._trace.stop()


# 同步入口：启动 CoreApp 事件循环
def run() -> None:
    asyncio.run(CoreApp().run())
