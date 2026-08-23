from __future__ import annotations

import asyncio
import json
import ssl
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from desk_agent.core.bus.envelope import JsonRpcRequest

type EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

_MAX_LINE_BYTES = 64 * 1024 * 1024  # 64 MB per frame，兼容 MCP 大文件工具结果


class IpcError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


class SocketClient:
    def __init__(self, host: str, port: int, *, ssl_context: ssl.SSLContext | None = None) -> None:
        self._host = host
        self._port = port
        self._ssl_context = ssl_context
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._event_handlers: list[EventHandler] = []
        # 每连接单调递增序号（防重放，从 1 开始；core.hello 为第 1 条请求）
        self._seq = 0
        self._client_id: str | None = None
        # 内部事件循环任务：由 start_event_loop() 启动，close() 负责取消
        self._loop_task: asyncio.Task[None] | None = None

    # 建立到 core 守护进程的 TCP 连接（传入 ssl_context 则走 TLS 并校验服务端证书）
    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port, limit=_MAX_LINE_BYTES, ssl=self._ssl_context
        )

    # 启动后台事件循环（读取响应/事件），返回任务；认证与命令收发前必须调用
    def start_event_loop(self) -> asyncio.Task[None]:
        self._loop_task = asyncio.create_task(self.run_event_loop())
        return self._loop_task

    @property
    def loop_task(self) -> asyncio.Task[None] | None:
        return self._loop_task

    # 阻塞直到连接断开（事件循环任务结束）
    async def wait_closed(self) -> None:
        if self._loop_task is not None:
            await self._loop_task

    # 关闭 TCP 连接并等待底层 socket 释放（同时取消内部事件循环任务）
    async def close(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await asyncio.wait_for(self._loop_task, timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
        if self._writer is not None:
            self._writer.close()
            try:
                await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
            except TimeoutError:
                pass

    # 注册服务器推送事件的回调，可多次调用以添加多个 handler
    def on_event(self, handler: EventHandler) -> None:
        self._event_handlers.append(handler)

    # 发送 core.hello 完成令牌握手；成功后连接进入 AUTHENTICATED 状态
    async def authenticate(self, token: str, client: str) -> dict[str, Any]:
        result = await self.send_command("core.hello", {"token": token, "client": client})
        self._client_id = str(result.get("client_id", ""))
        return result

    # 发送 JSON-RPC 命令并等待响应，成功返回 result dict，失败抛出 IpcError
    # seq/ts 覆盖参数供安全测试与协议扩展使用（正常调用无需传）
    async def send_command(
        self,
        method: str,
        params: dict[str, Any],
        *,
        seq: int | None = None,
        ts: int | None = None,
    ) -> dict[str, Any]:
        if self._writer is None:
            raise RuntimeError("not connected — call connect() first")
        self._seq += 1
        req_id = str(uuid.uuid4())
        request = JsonRpcRequest(
            id=req_id,
            method=method,
            params=params,
            seq=self._seq if seq is None else seq,
            ts=int(time.time()) if ts is None else ts,
        )
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        self._writer.write(request.model_dump_json().encode() + b"\n")
        await self._writer.drain()
        return await fut

    # 持续读取服务器消息，分发 RPC 响应到 pending future 或事件到 event handler
    async def run_event_loop(self) -> None:
        if self._reader is None:
            raise RuntimeError("not connected — call connect() first")
        try:
            while True:
                try:
                    line = await self._reader.readline()
                except (ConnectionResetError, OSError):
                    break
                except (ValueError, asyncio.LimitOverrunError):
                    # 单行超出 limit；丢弃本行，继续读取后续消息
                    continue
                if not line:
                    break
                await self._dispatch(line)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()
            self._pending.clear()

    # 解析单行消息并路由到 pending future（RPC 响应）或 event handler（服务器推送）
    async def _dispatch(self, line: bytes) -> None:
        try:
            msg: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            return

        if "jsonrpc" in msg:
            req_id: str | None = msg.get("id")
            if req_id and req_id in self._pending:
                fut = self._pending.pop(req_id)
                if not fut.done():
                    if "error" in msg:
                        err = msg["error"]
                        fut.set_exception(
                            IpcError(err.get("code", -1), err.get("message", "unknown"))
                        )
                    else:
                        fut.set_result(msg.get("result") or {})
        elif msg.get("kind") == "event":
            event_data: dict[str, Any] = msg.get("event", {})
            for handler in self._event_handlers:
                await handler(event_data)
