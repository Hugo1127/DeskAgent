"""客户端连接辅助：读 run 文件 → 构建 TLS 上下文（证书固定）→ 连接 → core.hello 认证。

CLI / TUI 统一走 `connect_authenticated`，保证每次连接都使用最新的
令牌 / 端口 / 证书（daemon 重启后 run 文件轮换，重连自动生效）。

令牌轮换竞态：若在 daemon 重启瞬间读到了旧 run 文件（旧令牌/旧证书），
认证会失败；此时重读 run 文件重试一次，保证跨重启连接的健壮性。
"""
from __future__ import annotations

import desk_agent
from desk_agent.core.config import DeskConfig
from desk_agent.core.transport.runfile import read_cert_pem, read_daemon_file
from desk_agent.core.transport.socket_client import IpcError, SocketClient
from desk_agent.core.transport.tls import client_context


# 建立到 daemon 的已认证连接；daemon 未就绪（run 文件缺失/损坏）时抛 ConnectionRefusedError
async def connect_authenticated(config: DeskConfig) -> SocketClient:
    for attempt in range(2):
        info = read_daemon_file()
        if info is None:
            raise ConnectionRefusedError(
                "daemon 未就绪：缺少 run 文件，请确认已运行 `desk core start`"
            )

        ssl_context = None
        if config.tls:
            cert_pem = read_cert_pem()
            if cert_pem is None:
                raise ConnectionRefusedError("TLS 已启用但缺少证书文件（~/.desk/run/desk.crt）")
            ssl_context = client_context(cert_pem)

        client = SocketClient(config.host, info.port, ssl_context=ssl_context)
        await client.connect()
        client.start_event_loop()
        if not config.auth:
            return client
        try:
            await client.authenticate(info.token, client=f"cli/{desk_agent.__version__}")
            return client
        except IpcError:
            # 令牌轮换竞态（读到旧 run 文件）：重读一次并重连
            await client.close()
            if attempt == 0:
                continue
            raise
    raise ConnectionRefusedError("daemon 认证失败（多次重试后仍无法通过 core.hello）")
