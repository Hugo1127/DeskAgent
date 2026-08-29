"""CoreBridge：desk-web 与 desk-core 守护进程之间的桥接层。

职责：
- 复用 `connect_authenticated()`（读 run 文件 → 证书固定 → core.hello 令牌认证）建立连接；
- 一次性订阅全局事件流，并把 daemon 事件扇出给所有 WebSocket 订阅者；
- 断线指数退避重连（每次重连重读 run 文件，daemon 重启后令牌/端口/证书轮换自动生效）；
- 封装命令发送（含超时），以及为运行回放打开独立连接的辅助方法。

安全边界：daemon 的令牌 / TLS 证书只存在于本进程内，绝不进入浏览器。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from desk_agent.core.config import DeskConfig
from desk_agent.core.transport.helpers import connect_authenticated
from desk_agent.core.transport.socket_client import IpcError, SocketClient

log = logging.getLogger(__name__)

# 事件回调：接收 daemon 事件原样 dict（不含信封）
EventSink = Callable[[dict[str, Any]], Awaitable[None]]
# 连接状态回调：True=已连接，False=离线
StatusSink = Callable[[bool], Awaitable[None]]

# 全局订阅主题（与 desk-tui 保持一致，覆盖 Web 界面所需的全部事件）
TOPICS = [
    "session.*",
    "run.*",
    "step.*",
    "tool.*",
    "llm.*",
    "permission.*",
    "subagent.*",
    "skill.*",
    "log.*",
    "context.*",
]

_DEFAULT_COMMAND_TIMEOUT = 120.0  # 单条命令超时（秒）；LLM run 期间 send_message 可长时间阻塞
_MAX_BACKOFF = 30.0
_INITIAL_BACKOFF = 1.0


class CoreBridge:
    def __init__(self, config: DeskConfig) -> None:
        self._config = config
        self._client: SocketClient | None = None
        self._connected = False
        self._event_sinks: list[EventSink] = []
        self._status_sinks: list[StatusSink] = []
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    # 注册事件回调（供 WebSocket 订阅实时事件流）
    def add_event_sink(self, sink: EventSink) -> None:
        self._event_sinks.append(sink)

    def remove_event_sink(self, sink: EventSink) -> None:
        if sink in self._event_sinks:
            self._event_sinks.remove(sink)

    # 注册连接状态回调
    def add_status_sink(self, sink: StatusSink) -> None:
        self._status_sinks.append(sink)

    def remove_status_sink(self, sink: StatusSink) -> None:
        if sink in self._status_sinks:
            self._status_sinks.remove(sink)

    # 在调用方的事件循环中启动桥接（后台任务管理连接生命周期）
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop())

    # 停止桥接并等待后台任务退出
    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # 发送命令并等待响应；daemon 离线时抛 ConnectionError。timeout=None 表示不超时
    # （session.send_message 会阻塞到整轮 LLM run 结束，需关闭超时）。
    async def send_command(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = _DEFAULT_COMMAND_TIMEOUT,
    ) -> dict[str, Any]:
        if self._client is None:
            raise ConnectionError("daemon offline")
        if timeout is None:
            return await self._client.send_command(method, params)
        return await asyncio.wait_for(
            self._client.send_command(method, params), timeout=timeout
        )

    # 为运行回放打开独立连接并订阅（含 replay_from_run）。
    # sink 在 subscribe 之前注册，保证回放事件不丢失；返回已订阅的 client 与订阅结果。
    async def open_replay_client(
        self, run_id: str, sink: EventSink
    ) -> tuple[SocketClient, dict[str, Any]]:
        client = await connect_authenticated(self._config)
        client.on_event(sink)
        try:
            result = await client.send_command(
                "event.subscribe",
                {"topics": TOPICS, "scope": "global", "replay_from_run": run_id},
            )
        except Exception:
            await client.close()
            raise
        return client, result

    # 变更连接状态并通知所有状态回调（状态未变化时不重复通知）
    async def _set_connected(self, value: bool) -> None:
        if self._connected == value:
            return
        self._connected = value
        for sink in list(self._status_sinks):
            try:
                await sink(value)
            except Exception:
                log.exception("status sink failed")

    # 连接生命周期主循环：连接 → 订阅 → 等待断开 → 指数退避重连
    async def _run_loop(self) -> None:
        backoff = _INITIAL_BACKOFF
        while not self._stop.is_set():
            try:
                client = await connect_authenticated(self._config)
            except (ConnectionRefusedError, OSError):
                await self._set_connected(False)
                await self._sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                continue

            log.info("connected to daemon %s:%s", self._config.host, self._config.port)
            self._client = client
            await self._set_connected(True)
            backoff = _INITIAL_BACKOFF

            client.on_event(self._dispatch_event)
            try:
                await client.send_command(
                    "event.subscribe", {"topics": TOPICS, "scope": "global"}
                )
                await client.wait_closed()
            except asyncio.CancelledError:
                raise
            except (IpcError, RuntimeError, OSError):
                pass
            finally:
                self._client = None
                await self._set_connected(False)
                try:
                    await client.close()
                except Exception:
                    pass

    # 把 daemon 事件扇出给所有事件回调
    async def _dispatch_event(self, event: dict[str, Any]) -> None:
        for sink in list(self._event_sinks):
            try:
                await sink(event)
            except Exception:
                log.exception("event sink failed")

    # 支持取消的休眠（等待重连间隔或停止信号）
    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass
