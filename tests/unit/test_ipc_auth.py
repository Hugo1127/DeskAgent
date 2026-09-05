"""IPC 通信安全单元测试：run 文件、自签证书与 TLS 握手、防重放校验。

注：沙箱环境会锁定 tempfile / pytest 创建的目录（无法删除），
因此测试使用共享 _TmpDir 在工作区自建临时目录，结束后清理。
"""
from __future__ import annotations

import asyncio
import os
import ssl
import time
from pathlib import Path

from _tmpdir import _TmpDir

from desk_agent.core.transport import runfile, tls
from desk_agent.core.transport.socket_server import check_seq, check_ts


def test_runfile_roundtrip() -> None:
    with _TmpDir() as d:
        info = runfile.DaemonInfo(
            token="tok-123", port=7437, pid=42,
            started_at="2026-05-16T00:00:00Z", version="0.0.1",
            cert_sha256="a" * 64,
        )
        runfile.write_daemon_file(info, run_dir=Path(d))
        got = runfile.read_daemon_file(run_dir=Path(d))
        assert got is not None
        assert got.token == "tok-123"
        assert got.port == 7437
        assert got.pid == 42
        assert got.cert_sha256 == "a" * 64

        if os.name != "nt":  # Windows 无 POSIX 权限位
            assert (Path(d) / "daemon.json").stat().st_mode & 0o777 == 0o600

        runfile.remove_daemon_file(run_dir=Path(d))
        assert runfile.read_daemon_file(run_dir=Path(d)) is None


def test_runfile_missing_returns_none() -> None:
    with _TmpDir() as d:
        assert runfile.read_daemon_file(run_dir=Path(d)) is None


def test_runfile_corrupt_returns_none() -> None:
    with _TmpDir() as d:
        (Path(d) / "daemon.json").write_text("{not json", encoding="utf-8")
        assert runfile.read_daemon_file(run_dir=Path(d)) is None


def test_token_rotation() -> None:
    with _TmpDir() as d:
        runfile.write_daemon_file(
            runfile.DaemonInfo(token="aaa", port=1, pid=1, started_at="t", version="v"),
            run_dir=Path(d),
        )
        assert runfile.read_daemon_file(run_dir=Path(d)).token == "aaa"

        # daemon 重启 → 新 token 覆盖旧 token
        runfile.write_daemon_file(
            runfile.DaemonInfo(token="bbb", port=2, pid=2, started_at="t", version="v"),
            run_dir=Path(d),
        )
        assert runfile.read_daemon_file(run_dir=Path(d)).token == "bbb"


def test_new_token_is_random_and_long_enough() -> None:
    assert runfile.new_token() != runfile.new_token()
    assert len(runfile.new_token()) >= 32  # 256-bit urlsafe


def test_check_seq() -> None:
    # 严格递增才通过
    assert check_seq(0, 1)[0]
    assert check_seq(5, 6)[0]
    # 重复 / 回退 / 缺失 / 非 int 均拒绝
    assert not check_seq(5, 5)[0]
    assert not check_seq(5, 4)[0]
    assert not check_seq(5, None)[0]
    assert not check_seq(5, "6")[0]  # type: ignore[arg-type]


def test_check_ts() -> None:
    now = int(time.time())
    assert check_ts(now)[0]
    assert check_ts(now - 30)[0]
    assert not check_ts(now - 600)[0]  # 过期
    assert not check_ts(now + 600)[0]  # 未来
    assert not check_ts(None)[0]
    assert not check_ts("123")[0]  # type: ignore[arg-type]


def test_self_signed_cert_and_tls_handshake() -> None:
    """生成自签证书，服务端/客户端 SSLContext 完成一次回环 TLS 握手（证书固定）。"""
    with _TmpDir() as d:
        async def run() -> list[bytes]:
            key_path = Path(d) / "desk.key"
            cert_path = Path(d) / "desk.crt"
            fingerprint = tls.generate_self_signed(
                ["127.0.0.1", "localhost"], key_path, cert_path
            )
            assert len(fingerprint) == 64  # SHA-256 hex
            assert key_path.exists() and cert_path.exists()
            if os.name != "nt":
                assert key_path.stat().st_mode & 0o777 == 0o600

            sctx = tls.server_context(cert_path, key_path)
            cctx = tls.client_context(cert_path.read_text(encoding="utf-8"))

            received: list[bytes] = []

            async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                data = await reader.read(5)
                received.append(data)
                writer.write(b"pong")
                await writer.drain()
                writer.close()

            server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=sctx)
            port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=cctx)
            writer.write(b"hello")
            await writer.drain()
            assert await reader.read(4) == b"pong"
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()
            return received

        assert asyncio.run(run()) == [b"hello"]


def test_client_context_rejects_other_cert() -> None:
    """客户端以证书 A 为信任锚时，服务端出示证书 B 必须握手失败（pinning 生效）。"""
    with _TmpDir() as d:
        cert_a = Path(d) / "a.crt"
        key_a = Path(d) / "a.key"
        cert_b = Path(d) / "b.crt"
        key_b = Path(d) / "b.key"
        tls.generate_self_signed(["127.0.0.1"], key_a, cert_a)
        tls.generate_self_signed(["127.0.0.1"], key_b, cert_b)

        async def run() -> None:
            sctx = tls.server_context(cert_b, key_b)
            cctx = tls.client_context(cert_a.read_text(encoding="utf-8"))
            server = await asyncio.start_server(
                lambda r, w: None, "127.0.0.1", 0, ssl=sctx
            )
            port = server.sockets[0].getsockname()[1]
            try:
                await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port, ssl=cctx), timeout=5.0
                )
                raise AssertionError("不应握手成功：证书 B 不在信任锚内")
            except (ssl.SSLError, ConnectionError, TimeoutError):
                pass  # 期望失败
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(run())
