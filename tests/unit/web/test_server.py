"""desk-web REST 端点单元测试：桩 daemon + TestClient，覆盖命令转发、Origin/CSRF 守卫、登录认证、runs 推导。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from desk_agent.core.config import DeskConfig
from desk_agent.web.server import WebServerConfig, create_app

_PING = {"server_version": "0.0.1", "uptime_ms": 123, "received_at": "2026-01-01T00:00:00Z"}


class _FakeBridge:
    """桩桥接：按方法名返回预设结果或抛预设异常，并记录调用。"""

    def __init__(
        self,
        responses: dict | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._responses = responses or {}
        self._exc = exc
        self.calls: list[tuple[str, dict]] = []

    def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_command(self, method: str, params: dict, *, timeout: float | None = None) -> dict:
        self.calls.append((method, params))
        if self._exc is not None:
            raise self._exc
        return self._responses.get(method, {})


def make_client(bridge: _FakeBridge, token: str = "") -> TestClient:
    config = DeskConfig()
    web_config = WebServerConfig(token=token)
    app = create_app(config, web_config, bridge)  # type: ignore[arg-type]
    return TestClient(app)


def test_bootstrap_returns_csrf_and_sets_cookie() -> None:
    with make_client(_FakeBridge()) as client:
        r = client.get("/api/bootstrap")
        assert r.status_code == 200
        body = r.json()
        assert body["auth_required"] is False
        assert body["csrf_token"]
        assert "desk_csrf" in r.cookies


def test_status_online_and_offline() -> None:
    with make_client(_FakeBridge(responses={"core.ping": _PING})) as client:
        r = client.get("/api/status")
        assert r.status_code == 200
        assert r.json() == {"online": True, "version": "0.0.1", "uptime_ms": 123, "received_at": _PING["received_at"]}

    with make_client(_FakeBridge(exc=ConnectionError("daemon offline"))) as client:
        r = client.get("/api/status")
        assert r.status_code == 200
        assert r.json() == {"online": False}


def test_mutating_requires_csrf() -> None:
    bridge = _FakeBridge(responses={"session.create": {"session_id": "sess-1", "status": "active"}})
    with make_client(bridge) as client:
        # 无 CSRF 头 → 403
        r = client.post("/api/sessions", json={"mode": "chat"})
        assert r.status_code == 403

        # 先 bootstrap 取 token，再带 X-CSRF-Token → 200
        csrf = client.get("/api/bootstrap").json()["csrf_token"]
        r = client.post("/api/sessions", json={"mode": "chat"}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        assert r.json() == {"session_id": "sess-1", "status": "active"}
        assert bridge.calls[0] == ("session.create", {"mode": "chat", "title": ""})


def test_cross_origin_rejected() -> None:
    bridge = _FakeBridge(responses={"session.create": {"session_id": "sess-1", "status": "active"}})
    with make_client(bridge) as client:
        csrf = client.get("/api/bootstrap").json()["csrf_token"]
        r = client.post(
            "/api/sessions",
            json={"mode": "chat"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://evil.example"},
        )
        assert r.status_code == 403


def test_send_message_forwards_with_content() -> None:
    bridge = _FakeBridge(responses={"session.send_message": {"run_id": "r1"}})
    with make_client(bridge) as client:
        csrf = client.get("/api/bootstrap").json()["csrf_token"]
        r = client.post(
            "/api/sessions/sess-1/messages",
            json={"content": "hello"},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200
        assert bridge.calls[0] == ("session.send_message", {"session_id": "sess-1", "content": "hello"})


def test_permission_respond_forwards() -> None:
    bridge = _FakeBridge(responses={"permission.respond": {"ok": True}})
    with make_client(bridge) as client:
        csrf = client.get("/api/bootstrap").json()["csrf_token"]
        r = client.post(
            "/api/permission/respond",
            json={"tool_use_id": "toolu_1", "decision": "allow_once"},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200
        assert bridge.calls[0] == (
            "permission.respond", {"tool_use_id": "toolu_1", "decision": "allow_once"}
        )


def test_permission_preview_forwards() -> None:
    bridge = _FakeBridge(responses={"permission.preview": {"ok": True, "decision": "ask"}})
    with make_client(bridge) as client:
        csrf = client.get("/api/bootstrap").json()["csrf_token"]
        r = client.post(
            "/api/permission/preview",
            json={
                "tool_use_id": "toolu_1",
                "tool_name": "bash",
                "params": {"command": "echo hi"},
                "session_id": "sess-1",
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "decision": "ask"}
        assert bridge.calls[0] == (
            "permission.preview",
            {
                "tool_use_id": "toolu_1",
                "tool_name": "bash",
                "params": {"command": "echo hi"},
                "session_id": "sess-1",
            },
        )


def test_daemon_offline_maps_to_503() -> None:
    bridge = _FakeBridge(exc=ConnectionError("daemon offline"))
    with make_client(bridge) as client:
        csrf = client.get("/api/bootstrap").json()["csrf_token"]
        r = client.get("/api/sessions")
        assert r.status_code == 503
        r = client.post("/api/sessions", json={"mode": "chat"}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 503


def test_auth_required_returns_401_without_token() -> None:
    bridge = _FakeBridge(responses={"core.ping": _PING})
    with make_client(bridge, token="secret") as client:
        assert client.get("/api/status").status_code == 401
        r = client.get("/api/status", headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200
        assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_runs_derived_from_sessions() -> None:
    bridge = _FakeBridge(responses={"session.list": {"sessions": [
        {"id": "sess-1", "title": "T", "status": "closed", "updated_at": "u",
         "run_ids": ["20260101-000000-a", "20260102-000000-b"]},
    ]}})
    with make_client(bridge) as client:
        r = client.get("/api/runs")
        assert r.status_code == 200
        runs = r.json()["runs"]
        # run_id 形如 YYYYMMDD-...，字典序即时间序，倒序
        assert [x["run_id"] for x in runs] == ["20260102-000000-b", "20260101-000000-a"]
        assert runs[0]["session_id"] == "sess-1"
