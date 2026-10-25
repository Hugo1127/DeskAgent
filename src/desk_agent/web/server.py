"""desk-web 的 FastAPI 应用：REST 命令转发 + /ws 事件流 + 静态托管 + 安全守卫。

设计要点
- 浏览器不接触 daemon 的 TLS / 令牌 / TCP，安全边界留在本桥接层；
- 数据访问一律走协议（daemon 命令），desk-web 不做文件系统读取；
- Origin / CSRF 守卫 + 可选 DESK_WEB_TOKEN 登录认证；
- 前端为无构建 vanilla JS，由 StaticFiles 托管。
"""
from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import desk_agent
from desk_agent.core.config import DeskConfig
from desk_agent.core.permissions.policy import PermissionResponseDecision
from desk_agent.core.transport.socket_client import IpcError
from desk_agent.web.bridge import CoreBridge

_STATIC_DIR = Path(__file__).parent / "static"
_CSRF_COOKIE = "desk_csrf"
_CSRF_HEADER = "X-CSRF-Token"

# daemon JSON-RPC 错误码 → HTTP 状态码映射
_IPC_HTTP_STATUS: dict[int, int] = {
    -32010: 404,  # SESSION_NOT_FOUND
    -32011: 409,  # SESSION_CLOSED
    -32012: 409,  # SESSION_BUSY
}


@dataclass
class WebServerConfig:
    host: str = "127.0.0.1"
    port: int = 9987
    # 非空时启用登录认证（远程浏览器访问场景）；空表示免登录（仅限绑回环）
    token: str = ""


# ---- 请求体模型 ----
class CreateSessionBody(BaseModel):
    mode: str = "chat"
    title: str = ""


class SendMessageBody(BaseModel):
    content: str


class PermissionRespondBody(BaseModel):
    tool_use_id: str
    # 6 种合法决策；非法值由 Pydantic 返回 422，而不是静默按拒绝处理
    decision: PermissionResponseDecision


class PermissionPreviewBody(BaseModel):
    tool_use_id: str
    tool_name: str
    params: dict[str, Any] = {}
    session_id: str = ""


class CompactBody(BaseModel):
    focus: str = ""


class LoginBody(BaseModel):
    token: str


def _new_token() -> str:
    return secrets.token_urlsafe(32)


# Origin 头（若存在）必须与请求 Host 一致，拦截跨站请求
def _origin_mismatch(origin: str | None, host: str | None) -> bool:
    if not origin or not host:
        return False
    try:
        return urlparse(origin).netloc != host
    except ValueError:
        return True


def _constant_eq(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)


def _build_app(config: DeskConfig, web_config: WebServerConfig, bridge: CoreBridge) -> FastAPI:
    token = web_config.token

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        bridge.start()
        yield
        await bridge.stop()

    app = FastAPI(title="desk-web", version=desk_agent.__version__, lifespan=lifespan)

    # ---- 安全守卫 ----
    def require_auth(request: Request) -> None:
        if not token:
            return
        auth = request.headers.get("authorization", "")
        if not _constant_eq(auth, f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="unauthorized")

    def require_csrf(request: Request) -> None:
        if _origin_mismatch(
            request.headers.get("origin"), request.headers.get("host")
        ):
            raise HTTPException(status_code=403, detail="cross-origin request rejected")
        cookie = request.cookies.get(_CSRF_COOKIE, "")
        header = request.headers.get(_CSRF_HEADER, "")
        if not cookie or not header or not _constant_eq(cookie, header):
            raise HTTPException(status_code=403, detail="invalid CSRF token")

    # 统一命令转发：把 daemon 异常映射为 HTTP 错误
    async def call(
        method: str, params: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        try:
            return await bridge.send_command(method, params, timeout=timeout)
        except ConnectionError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail="daemon command timed out") from e
        except IpcError as e:
            raise HTTPException(
                status_code=_IPC_HTTP_STATUS.get(e.code, 400), detail=str(e)
            ) from e

    # ---- REST 端点 ----
    @app.get("/api/bootstrap")
    async def bootstrap(request: Request, response: Response) -> dict[str, Any]:
        csrf = _new_token()
        response.set_cookie(
            _CSRF_COOKIE, csrf, httponly=True, samesite="strict", path="/"
        )
        return {
            "csrf_token": csrf,
            "auth_required": bool(token),
            "version": desk_agent.__version__,
            "default_model": config.llm.default_model,
            "max_steps": config.agent.max_steps,
            "permission_timeout_s": config.permission.timeout_s,
            "daemon_host": config.host,
            "daemon_port": config.port,
        }

    @app.post("/api/login")
    async def login(body: LoginBody) -> dict[str, Any]:
        if not token:
            return {"ok": True}
        if not _constant_eq(body.token, token):
            raise HTTPException(status_code=401, detail="invalid token")
        return {"ok": True}

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        require_auth(request)
        try:
            result = await bridge.send_command("core.ping", {"client": "web/0.0.1"})
        except (ConnectionError, TimeoutError, IpcError):
            return {"online": False}
        return {
            "online": True,
            "version": result.get("server_version", ""),
            "uptime_ms": result.get("uptime_ms", 0),
            "received_at": result.get("received_at", ""),
        }

    @app.get("/api/sessions")
    async def list_sessions(request: Request) -> dict[str, Any]:
        require_auth(request)
        return await call("session.list", {})

    @app.post("/api/sessions")
    async def create_session(request: Request, body: CreateSessionBody) -> dict[str, Any]:
        require_auth(request)
        require_csrf(request)
        return await call("session.create", {"mode": body.mode, "title": body.title})

    @app.get("/api/sessions/{session_id}/history")
    async def session_history(request: Request, session_id: str) -> dict[str, Any]:
        require_auth(request)
        return await call("session.get_history", {"session_id": session_id})

    @app.post("/api/sessions/{session_id}/messages")
    async def send_message(
        request: Request, session_id: str, body: SendMessageBody
    ) -> dict[str, Any]:
        require_auth(request)
        require_csrf(request)
        # send_message 阻塞到整轮 run 结束，关闭命令超时（进度由 /ws 事件流驱动）
        return await call(
            "session.send_message",
            {"session_id": session_id, "content": body.content},
            timeout=None,
        )

    @app.post("/api/sessions/{session_id}/close")
    async def close_session(request: Request, session_id: str) -> dict[str, Any]:
        require_auth(request)
        require_csrf(request)
        return await call("session.close", {"session_id": session_id})

    @app.post("/api/sessions/{session_id}/compact")
    async def compact_session(
        request: Request, session_id: str, body: CompactBody
    ) -> dict[str, Any]:
        require_auth(request)
        require_csrf(request)
        return await call(
            "session.compact", {"session_id": session_id, "focus": body.focus}
        )

    @app.post("/api/permission/respond")
    async def permission_respond(
        request: Request, body: PermissionRespondBody
    ) -> dict[str, Any]:
        require_auth(request)
        require_csrf(request)
        return await call(
            "permission.respond",
            {"tool_use_id": body.tool_use_id, "decision": body.decision},
        )

    @app.post("/api/permission/preview")
    async def permission_preview(
        request: Request, body: PermissionPreviewBody
    ) -> dict[str, Any]:
        require_auth(request)
        require_csrf(request)
        return await call(
            "permission.preview",
            {
                "tool_use_id": body.tool_use_id,
                "tool_name": body.tool_name,
                "params": body.params,
                "session_id": body.session_id,
            },
        )

    @app.get("/api/runs")
    async def list_runs(request: Request) -> dict[str, Any]:
        require_auth(request)
        sessions = await call("session.list", {})
        runs: list[dict[str, Any]] = []
        for s in sessions.get("sessions", []):
            for run_id in s.get("run_ids", []):
                runs.append(
                    {
                        "run_id": str(run_id),
                        "session_id": s.get("id", ""),
                        "session_title": s.get("title", ""),
                        "session_updated_at": s.get("updated_at", ""),
                    }
                )
        # run_id 形如 YYYYMMDD-HHMMSS-xxxxxx，字典序即时间序
        runs.sort(key=lambda r: r["run_id"], reverse=True)
        return {"runs": runs}

    # ---- WebSocket /ws ----
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        host = ws.headers.get("host")
        if _origin_mismatch(origin, host):
            await ws.close(code=1008)
            return
        cookie = ws.cookies.get(_CSRF_COOKIE, "")
        csrf = ws.query_params.get("csrf", "")
        if not cookie or not csrf or not _constant_eq(cookie, csrf):
            await ws.close(code=1008)
            return
        if token:
            ws_token = ws.query_params.get("token", "")
            if not _constant_eq(ws_token, token):
                await ws.close(code=1008)
                return
        await ws.accept()
        replay = ws.query_params.get("replay")
        if replay:
            await _serve_replay(ws, bridge, replay)
        else:
            await _serve_live(ws, bridge)

    # 静态托管（API/WS 路由注册在前，优先匹配；其余命中 static）
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app


async def _ws_receiver(ws: WebSocket) -> None:
    """在后台接收 WS 消息，客户端断开时返回（用于探测断连）。"""
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
    except (RuntimeError, OSError):
        return


async def _serve_live(ws: WebSocket, bridge: CoreBridge) -> None:
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def on_event(event: dict[str, Any]) -> None:
        await queue.put(("event", event))

    async def on_status(connected: bool) -> None:
        await queue.put(("status", connected))

    bridge.add_event_sink(on_event)
    bridge.add_status_sink(on_status)
    recv_task = asyncio.create_task(_ws_receiver(ws))
    try:
        await ws.send_json({"kind": "status", "connected": bridge.connected})
        while True:
            get_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                [get_task, recv_task], return_when=asyncio.FIRST_COMPLETED
            )
            if recv_task in done:
                get_task.cancel()
                break
            kind, payload = get_task.result()
            if kind == "status":
                await ws.send_json({"kind": "status", "connected": payload})
            else:
                await ws.send_json({"kind": "event", "event": payload})
    finally:
        recv_task.cancel()
        bridge.remove_event_sink(on_event)
        bridge.remove_status_sink(on_status)


async def _serve_replay(ws: WebSocket, bridge: CoreBridge, run_id: str) -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def on_event(event: dict[str, Any]) -> None:
        await queue.put(event)

    try:
        client, result = await bridge.open_replay_client(run_id, on_event)
    except (ConnectionRefusedError, OSError, IpcError):
        await ws.send_json({"kind": "replay_end", "run_id": run_id, "error": True})
        return

    recv_task = asyncio.create_task(_ws_receiver(ws))
    try:
        # 回放事件在 subscribe 返回前已全部入队，先排空再发 replay_end
        while True:
            try:
                event = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await ws.send_json({"kind": "event", "event": event})
        await ws.send_json(
            {
                "kind": "replay_end",
                "run_id": run_id,
                "replayed_count": result.get("replayed_count", 0),
            }
        )
        # 回放结束后继续接收实时事件，直到客户端断开
        while True:
            get_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                [get_task, recv_task], return_when=asyncio.FIRST_COMPLETED
            )
            if recv_task in done:
                get_task.cancel()
                break
            await ws.send_json({"kind": "event", "event": get_task.result()})
    finally:
        recv_task.cancel()
        await client.close()


# 构建 FastAPI 应用（供 desk-web 入口与测试使用）
def create_app(config: DeskConfig, web_config: WebServerConfig, bridge: CoreBridge) -> FastAPI:
    return _build_app(config, web_config, bridge)
