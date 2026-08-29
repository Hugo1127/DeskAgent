"""session.list 命令与 get_history 放宽（磁盘加载历史会话）的单元测试。"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from desk_agent.core.bus.envelope import HandlerError
from desk_agent.core.events.bus import EventBus
from desk_agent.core.session.manager import SessionManager
from desk_agent.core.session.model import Session
from desk_agent.core.session.store import SessionStore


class _TmpDir:
    def __init__(self) -> None:
        self.path = Path(f".tmp-sess-{uuid.uuid4().hex[:12]}")
        self.path.mkdir()

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def _session(sid: str, updated_at: str) -> Session:
    return Session(
        id=sid,
        mode="chat",
        status="waiting_for_input",
        title="t",
        created_at="2026-01-01T00:00:00Z",
        updated_at=updated_at,
        run_ids=["r1", "r2"],
    )


def _manager(root: Path) -> SessionManager:
    store = SessionStore(root)
    return SessionManager(store, runner_factory=lambda: None, bus=EventBus())


def test_store_list_sessions_sorted_by_updated_at() -> None:
    with _TmpDir() as d:
        store = SessionStore(d)
        store.write_meta(_session("sess-old", "2026-01-01T00:00:00Z"))
        store.write_meta(_session("sess-new", "2026-02-01T00:00:00Z"))
        sessions = store.list_sessions()
        assert [s.id for s in sessions] == ["sess-new", "sess-old"]
        assert len(sessions[0].run_ids) == 2  # run_count 由 handler 推导


def test_store_list_sessions_skips_broken_meta() -> None:
    with _TmpDir() as d:
        store = SessionStore(d)
        store.write_meta(_session("sess-ok", "2026-01-01T00:00:00Z"))
        broken = d / "sess-broken"
        broken.mkdir()
        (broken / "meta.json").write_text("{bad json", encoding="utf-8")
        assert [s.id for s in store.list_sessions()] == ["sess-ok"]


def test_manager_list_sessions_delegates_to_store() -> None:
    with _TmpDir() as d:
        store = SessionStore(d)
        store.write_meta(_session("sess-a", "2026-01-01T00:00:00Z"))
        mgr = _manager(d)
        assert [s.id for s in mgr.list_sessions()] == ["sess-a"]


def test_get_history_loads_from_disk_after_restart() -> None:
    """session 不在内存（模拟 daemon 重启）时，get_history 仍能按磁盘 meta.json 读取历史。"""
    with _TmpDir() as d:
        store = SessionStore(d)
        sid = "sess-abc"
        store.write_meta(_session(sid, "2026-01-01T00:00:00Z"))
        store.append_message(sid, "user", "hello")
        store.append_message(sid, "assistant", "hi")

        mgr = _manager(d)  # 未调用 create()，内存索引为空
        messages = asyncio.run(mgr.get_history(sid))
        assert [m["content"] for m in messages] == ["hello", "hi"]
        # 加载后进入内存缓存，可直接取到
        assert mgr._get_session(sid).id == sid  # type: ignore[attr-defined]


def test_get_history_missing_session_raises_not_found() -> None:
    with _TmpDir() as d:
        mgr = _manager(d)
        try:
            asyncio.run(mgr.get_history("sess-missing"))
        except HandlerError as e:
            assert e.code == -32010  # SESSION_NOT_FOUND
        else:
            raise AssertionError("expected HandlerError for missing session")
