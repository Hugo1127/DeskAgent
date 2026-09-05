from __future__ import annotations

import asyncio
import hmac
import json
import logging
import ssl
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

import desk_agent
from desk_agent.core.bus.envelope import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    STALE_SEQUENCE,
    UNAUTHENTICATED,
    HandlerError,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcSuccess,
    make_error,
)
from desk_agent.core.trace.record import TraceRecord
from desk_agent.core.trace.writer import TraceWriter
from desk_agent.core.transport.ipc_broadcaster import IpcEventBroadcaster
from desk_agent.core.utils import now_utc as _now

logger = logging.getLogger(__name__)

type CommandHandler = Callable[[dict[str, Any]], Awaitable[Any]]

# IPC 协议版本（core.hello 握手时协商，见 docs/COMMUNICATION_SECURITY.md）
PROTOCOL_VERSION = 1
# 请求时间戳可接受的最大偏差（秒），超出视为过期重放
_TS_WINDOW_S = 60

# 每个连接处理协程中，当前正在处理的 writer（供 handler 读取连接上下文）
_writer_var: ContextVar[asyncio.StreamWriter] = ContextVar("_writer_var")


# 返回当前 handler 调用所属连接的 StreamWriter
def get_connection_writer() -> asyncio.StreamWriter:
    return _writer_var.get()


# 校验请求序号：必须为严格递增（seq > last_seq），否则视为重放/乱序
def check_seq(last_seq: int, seq: int | None) -> tuple[bool, str]:
    if seq is None:
        return False, "missing seq"
    if not isinstance(seq, int) or seq <= last_seq:
        return False, f"stale or duplicate seq: {seq} (last={last_seq})"
    return True, ""


# 校验请求时间戳：与服务器时钟偏差超过窗口视为过期重放
def check_ts(ts: int | None, *, now: int | None = None) -> tuple[bool, str]:
    if ts is None:
        return False, "missing ts"
    if not isinstance(ts, int):
        return False, f"invalid ts: {ts!r}"
    now = int(datetime.now(UTC).timestamp()) if now is None else now
    if abs(now - ts) > _TS_WINDOW_S:
        return False, f"ts out of window: {ts} (now={now})"
    return True, ""


@dataclass
class _ConnState:
    writer: asyncio.StreamWriter
    authenticated: bool = False
    client_id: str | None = None
    client_label: str | None = None
    last_seq: int = 0


_MAX_LINE_BYTES = 64 * 1024 * 1024  # 64 MB per frame，兼容 MCP 大文件工具结果


class SocketServer:
    def __init__(
        self,
        host: str,
        port: int,
        broadcaster: IpcEventBroadcaster | None = None,
        trace: TraceWriter | None = None,
        *,
        # 令牌认证：token=None 表示关闭认证（仅调试用）；否则未认证连接只允许 core.hello
        token: str | None = None,
        # TLS：传入则 start_server 以 SSL 包装（传输加密 + 证书固定）
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._ssl_context = ssl_context
        self._handlers: dict[str, CommandHandler] = {}
        self._server: asyncio.AbstractServer | None = None
        self._broadcaster = broadcaster
        self._trace = trace
        self._active_writers: set[asyncio.StreamWriter] = set()
        # 正在执行的命令处理任务（长时间运行的 handler，如 session.send_message 的 LLM run）
        self._tasks: set[asyncio.Task[Any]] = set()

    # 注册一个方法名对应的命令处理函数
    def register(self, method: str, handler: CommandHandler) -> None:
        self._handlers[method] = handler

    # 启动 TCP 服务器；若端口已被占用则退出进程
    async def start(self) -> str:
        try:
            _r, w = await asyncio.open_connection(self._host, self._port)
            w.close()
            await w.wait_closed()
            raise SystemExit(f"core already running at {self._host}:{self._port}")
        except (ConnectionRefusedError, OSError):
            pass

        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self._host,
            port=self._port,
            limit=_MAX_LINE_BYTES,
            ssl=self._ssl_context,
        )
        return f"{self._host}:{self._port}"

    # 关闭服务器：先取消仍在执行的命令任务，再断开所有活跃连接，最后等待服务器关闭（各步均限时）
    async def stop(self) -> None:
        if self._server is None:
            return
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True), timeout=3.0
                )
            except (TimeoutError, asyncio.CancelledError):
                logger.warning("server.stop: %d handler task(s) did not finish in time",
                               len(self._tasks))
        for writer in list(self._active_writers):
            try:
                writer.close()
            except Exception:
                pass
        self._server.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
        except (TimeoutError, asyncio.CancelledError):
            pass

    # 处理单个客户端连接，完成后关闭写流；连接处理协程异常仅记录，不抛未处理异常
    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername", "<unknown>")
        logger.debug("client connected: %s", peer)
        state = _ConnState(writer=writer)
        self._active_writers.add(writer)
        try:
            await self._read_loop(reader, writer, state)
        except Exception:
            logger.exception("connection handler crashed peer=%s", peer)
        finally:
            self._active_writers.discard(writer)
            if self._broadcaster is not None:
                self._broadcaster.unsubscribe(writer)
            try:
                writer.close()
            except Exception:
                pass
            logger.debug("client disconnected: %s", peer)

    # 持续读取换行分隔的 JSON 行并逐行分发处理
    async def _read_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        state: _ConnState,
    ) -> None:
        while True:
            try:
                line = await reader.readline()
            except asyncio.LimitOverrunError:
                await self._send(
                    writer, state, make_error(None, INVALID_REQUEST, "Request too large")
                )
                return
            except (ConnectionResetError, BrokenPipeError, OSError):
                # 客户端异常断开（如进程被强杀触发 RST），视为正常断连
                logger.debug("client connection lost during read")
                return

            if not line:
                return

            # 每条命令独立作为 task 执行，避免长时间运行的 handler（如 session.send_message）
            # 阻塞读循环，使 permission.respond 等并发命令能被及时处理
            task = asyncio.create_task(self._handle_line(line, writer, state))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    # 解析单行 JSON-RPC 请求：先做传输层防重放校验，再做认证门禁，最后调用 handler
    async def _handle_line(
        self,
        line: bytes,
        writer: asyncio.StreamWriter,
        state: _ConnState,
    ) -> None:
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError as e:
            await self._send(writer, state, make_error(None, PARSE_ERROR, f"Parse error: {e}"))
            return

        try:
            req = JsonRpcRequest.model_validate(raw)
        except ValidationError as e:
            await self._send(
                writer, state,
                make_error(None, INVALID_REQUEST, "Invalid Request", str(e)),
            )
            return

        # 传输层防重放：序号严格递增 + 时间戳窗口（TLS 管不到的报文重放，见设计文档 5.8）
        seq_ok, seq_msg = check_seq(state.last_seq, req.seq)
        ts_ok, ts_msg = check_ts(req.ts)
        if not seq_ok or not ts_ok:
            msg = seq_msg if not seq_ok else ts_msg
            await self._send(writer, state, make_error(req.id, STALE_SEQUENCE, msg))
            return
        assert req.seq is not None
        state.last_seq = req.seq

        if self._trace is not None:
            client_id = state.client_id or str(writer.get_extra_info("peername", "<unknown>"))
            # core.hello 的 token 绝不允许进入 trace（防令牌落盘泄露）
            params: dict[str, Any] = req.params
            if req.method == "core.hello":
                params = {"client": params.get("client")}
            self._trace.emit(
                TraceRecord(
                    ts=_now(),
                    direction="CLIENT→CORE",
                    layer="ipc",
                    kind="command",
                    client_id=client_id,
                    data={"method": req.method, "id": req.id, "params": params},
                )
            )

        # 认证门禁：未认证连接只允许 core.hello（认证关闭时 token=None 直接放行）
        if req.method == "core.hello":
            if state.authenticated:
                await self._send(
                    writer, state,
                    make_error(req.id, INVALID_REQUEST, "already authenticated"),
                )
                return
            await self._handle_hello(req, writer, state)
            return

        if self._token is not None and not state.authenticated:
            await self._send(
                writer,
                state,
                make_error(
                    req.id,
                    UNAUTHENTICATED,
                    "authentication required: send core.hello first",
                ),
            )
            # 未认证连接即拒绝并断开，防止占用资源
            writer.close()
            return

        handler = self._handlers.get(req.method)
        if handler is None:
            await self._send(
                writer,
                state,
                make_error(req.id, METHOD_NOT_FOUND, f"Method not found: {req.method}"),
            )
            return

        _writer_var.set(writer)
        try:
            result = await handler(req.params)
        except HandlerError as e:
            await self._send(writer, state, make_error(req.id, e.code, str(e), e.data))
            return
        except ValidationError as e:
            await self._send(
                writer,
                state,
                make_error(req.id, INVALID_REQUEST, "Invalid params", str(e)),
            )
            return
        except Exception as e:
            logger.exception("handler %s raised: %s", req.method, e)
            await self._send(writer, state, make_error(req.id, INTERNAL_ERROR, "Internal error"))
            return

        result_data: Any = result.model_dump() if isinstance(result, BaseModel) else result
        try:
            await self._send(writer, state, JsonRpcSuccess(id=req.id, result=result_data))
        except (ConnectionResetError, BrokenPipeError, OSError):
            logger.debug("client disconnected before response for %s", req.method)

    # 处理 core.hello：常数时间校验令牌，成功后将连接置为 AUTHENTICATED
    async def _handle_hello(
        self,
        req: JsonRpcRequest,
        writer: asyncio.StreamWriter,
        state: _ConnState,
    ) -> None:
        token = req.params.get("token")
        client = req.params.get("client")

        # 认证关闭：任何 hello 都接受（兼容 auth=false 调试模式）
        if self._token is None:
            state.authenticated = True
            state.client_id = uuid.uuid4().hex
            state.client_label = str(client)
            await self._send(
                writer, state,
                JsonRpcSuccess(id=req.id, result=self._hello_result(state)),
            )
            return

        if not isinstance(token, str) or not isinstance(client, str) or not hmac.compare_digest(
            token.encode(), self._token.encode()
        ):
            logger.warning(
                "core.hello rejected peer=%s",
                writer.get_extra_info("peername", "<unknown>"),
            )
            await self._send(
                writer, state,
                make_error(req.id, UNAUTHENTICATED, "invalid token"),
            )
            writer.close()
            return

        state.authenticated = True
        state.client_id = uuid.uuid4().hex
        state.client_label = client
        logger.info("core.hello ok client=%s client_id=%s", client, state.client_id)
        await self._send(writer, state, JsonRpcSuccess(id=req.id, result=self._hello_result(state)))

    # 构造 hello 成功响应
    def _hello_result(self, state: _ConnState) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "server_version": desk_agent.__version__,
            "client_id": state.client_id,
        }

    # 将 pydantic 消息序列化为 JSON 行并写入流，随后刷新缓冲区
    async def _send(
        self,
        writer: asyncio.StreamWriter,
        state: _ConnState,
        msg: BaseModel,
    ) -> None:
        writer.write(msg.model_dump_json().encode() + b"\n")
        await writer.drain()
        if self._trace is not None:
            kind = "error" if isinstance(msg, JsonRpcError) else "response"
            client_id = state.client_id or str(writer.get_extra_info("peername", "<unknown>"))
            self._trace.emit(
                TraceRecord(
                    ts=_now(),
                    direction="CORE→CLIENT",
                    layer="ipc",
                    kind=kind,
                    client_id=client_id,
                    data=msg.model_dump(),
                )
            )
