"""SocketServer.start 端口检测单元测试：直接绑定，占用/不可用时抛 SystemExit。"""
from __future__ import annotations

import asyncio

import pytest

from desk_agent.core.transport.socket_server import SocketServer


async def test_start_binds_successfully() -> None:
    srv = SocketServer("127.0.0.1", 0, broadcaster=None)
    addr = await srv.start()
    assert srv._server is not None  # type: ignore[attr-defined]
    assert addr == "127.0.0.1:0"
    await srv.stop()


def test_start_raises_system_exit_when_port_in_use() -> None:
    async def _scenario() -> None:
        blocker = await asyncio.start_server(
            lambda r, w: None, host="127.0.0.1", port=0
        )
        port = blocker.sockets[0].getsockname()[1]
        try:
            srv = SocketServer("127.0.0.1", port, broadcaster=None)
            with pytest.raises(SystemExit, match="already running or port unavailable"):
                await srv.start()
        finally:
            blocker.close()
            await blocker.wait_closed()

    asyncio.run(_scenario())
