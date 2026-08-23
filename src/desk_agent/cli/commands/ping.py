from __future__ import annotations

import asyncio
import sys
import time

import desk_agent
from desk_agent.core.bus.commands import PongResult
from desk_agent.core.config import DeskConfig
from desk_agent.core.transport.helpers import connect_authenticated
from desk_agent.core.transport.socket_client import IpcError


# 同步入口：运行 ping 协程，连接失败时打印错误并退出
def cmd_ping(config: DeskConfig) -> None:
    try:
        asyncio.run(_ping(config))
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        sys.exit(1)
    except IpcError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


# 向 core 守护进程发送 ping 请求（读 run 文件 + TLS + 令牌握手），打印 pong 响应及延迟
async def _ping(config: DeskConfig) -> None:
    t0 = time.monotonic()
    client = await connect_authenticated(config)
    try:
        result = await client.send_command(
            "core.ping", {"client": f"cli/{desk_agent.__version__}"}
        )
    finally:
        await client.close()
    latency_ms = int((time.monotonic() - t0) * 1000)

    resp = PongResult.model_validate(result)
    print(f"pong server={resp.server_version} uptime={resp.uptime_ms}ms latency={latency_ms}ms")
