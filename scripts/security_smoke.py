#!/usr/bin/env python3
"""DeskAgent IPC 安全攻击模拟脚本（红队验收）。

用法（需先启动 daemon）：
    uv run desk core start
    uv run python scripts/security_smoke.py

脚本模拟以下攻击场景并断言被安全机制拦截：

    S1 无令牌连接直接发命令             → 期望 -32001 UNAUTHENTICATED
    S2 错误令牌握手                     → 期望 -32001 UNAUTHENTICATED
    S3 明文连接（TLS 开启时）           → 期望 TLS 握手失败 / 连接被拒
    S4 正确令牌全链路（ping + 建会话）  → 期望成功（正路径回归）
    S5 未认证伪造 permission.respond    → 期望 -32001（审批通道不可伪造）
    S6 同连接重放相同 seq               → 期望 -32005 STALE_SEQUENCE（防重放）
    S7 过期时间戳报文                   → 期望 -32005 STALE_SEQUENCE（防重放）

退出码：0 = 全部通过；1 = 存在失败；2 = daemon 未运行 / 环境不满足。

注：本脚本只验证「网络传输 + 协议层」防御；本机同用户恶意进程（可读 run 文件令牌）
不在本脚本覆盖范围内（见 docs/COMMUNICATION_SECURITY.md）。
"""
from __future__ import annotations

import asyncio
import json
import ssl
import sys
import time
from typing import Any

import desk_agent
from desk_agent.core.config import get_config
from desk_agent.core.transport.runfile import read_cert_pem, read_daemon_file
from desk_agent.core.transport.socket_client import IpcError, SocketClient
from desk_agent.core.transport.tls import client_context

# 期望的错误码（与 core/bus/envelope.py 一致）
UNAUTHENTICATED = -32001
STALE_SEQUENCE = -32005

_PASS = "PASS"
_FAIL = "FAIL"
_SKIP = "SKIP"
_results: list[tuple[str, str, str]] = []


def _record(name: str, status: str, detail: str = "") -> None:
    _results.append((name, status, detail))
    print(f"  [{status:4}] {name}" + (f"  ({detail})" if detail else ""))


# 构建带 TLS 的 SocketClient（TLS 开启时以 run 文件证书为信任锚 pinning）
def _new_client(config: Any, port: int) -> SocketClient:
    ssl_context = client_context(read_cert_pem()) if config.tls else None
    return SocketClient(config.host, port, ssl_context=ssl_context)


# 建立连接并启动事件循环任务；auth=True 时先完成令牌握手
async def _open_client(
    config: Any, port: int, token: str, *, auth: bool = True, hello_token: str | None = None
) -> tuple[SocketClient, asyncio.Task[Any]]:
    client = _new_client(config, port)
    await client.connect()
    loop_task = asyncio.create_task(client.run_event_loop())
    if auth:
        await client.authenticate(hello_token or token, client=f"smoke/{desk_agent.__version__}")
    return client, loop_task


async def _close(client: SocketClient, loop_task: asyncio.Task[Any]) -> None:
    if not loop_task.done():
        loop_task.cancel()
    await client.close()


async def _scenario_s1(config: Any, port: int) -> None:
    """无令牌连接直接发命令 → 期望 -32001"""
    try:
        client, loop_task = await _open_client(config, port, "", auth=False)
        try:
            await client.send_command("core.ping", {"client": "attacker"})
            _record("S1 无令牌连接直接发命令", _FAIL, "未认证请求被接受！")
        except IpcError as e:
            _record("S1 无令牌连接直接发命令",
                    _PASS if e.code == UNAUTHENTICATED else _FAIL, f"code={e.code}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S1 无令牌连接直接发命令", _FAIL, f"异常: {e}")


async def _scenario_s2(config: Any, port: int) -> None:
    """错误令牌握手 → 期望 -32001"""
    try:
        client = _new_client(config, port)
        await client.connect()
        loop_task = asyncio.create_task(client.run_event_loop())
        try:
            await client.authenticate("wrong-token", client="smoke")
            _record("S2 错误令牌握手", _FAIL, "错误令牌被接受！")
        except IpcError as e:
            _record("S2 错误令牌握手",
                    _PASS if e.code == UNAUTHENTICATED else _FAIL, f"code={e.code}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S2 错误令牌握手", _FAIL, f"异常: {e}")


async def _scenario_s3(config: Any, port: int) -> None:
    """明文连接（TLS 开启时）→ 期望握手失败"""
    if not config.tls:
        _record("S3 明文连接（TLS 关闭）", _SKIP, "tls=off，无明文防护可验证")
        return
    req = {
        "jsonrpc": "2.0",
        "id": "plain-1",
        "method": "core.hello",
        "params": {"token": "x", "client": "plain"},
        "seq": 1,
        "ts": int(time.time()),
    }
    reader, writer = await asyncio.open_connection(config.host, port)
    try:
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=3.0)
        if line:
            _record("S3 明文连接", _FAIL, f"服务端接受了明文并返回: {line[:60]!r}")
        else:
            _record("S3 明文连接", _PASS, "服务端关闭连接（EOF）")
    except (TimeoutError, ssl.SSLError, ConnectionResetError, BrokenPipeError, OSError):
        _record("S3 明文连接", _PASS, "TLS 握手失败/连接被拒")
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _scenario_s4(config: Any, port: int, token: str) -> None:
    """正确令牌全链路 → 期望成功"""
    try:
        client, loop_task = await _open_client(config, port, token)
        try:
            pong = await client.send_command("core.ping", {"client": f"smoke/{desk_agent.__version__}"})
            created = await client.send_command("session.create", {"mode": "chat"})
            sid = str(created.get("session_id", "?"))[:8]
            _record("S4 正确令牌全链路", _PASS, f"ping ok, session={sid}, server={pong.get('server_version')}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S4 正确令牌全链路", _FAIL, f"异常: {e}")


async def _scenario_s5(config: Any, port: int) -> None:
    """未认证伪造 permission.respond → 期望 -32001"""
    try:
        client, loop_task = await _open_client(config, port, "", auth=False)
        try:
            await client.send_command(
                "permission.respond",
                {"tool_use_id": "toolu_fake_0001", "decision": "always_allow"},
            )
            _record("S5 伪造权限审批", _FAIL, "未认证的 permission.respond 被接受！")
        except IpcError as e:
            _record("S5 伪造权限审批",
                    _PASS if e.code == UNAUTHENTICATED else _FAIL, f"code={e.code}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S5 伪造权限审批", _FAIL, f"异常: {e}")


async def _scenario_s6(config: Any, port: int, token: str) -> None:
    """同连接重放相同 seq → 期望 -32005"""
    try:
        client, loop_task = await _open_client(config, port, token)
        try:
            # 第一次正常请求会占用一个 seq；随后显式复用相同 seq 重放
            await client.send_command("core.ping", {"client": "smoke"})
            try:
                await client.send_command("core.ping", {"client": "smoke"}, seq=client._seq)  # type: ignore[attr-defined]
                _record("S6 重放相同 seq", _FAIL, "重复 seq 被接受！")
            except IpcError as e:
                _record("S6 重放相同 seq",
                        _PASS if e.code == STALE_SEQUENCE else _FAIL, f"code={e.code}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S6 重放相同 seq", _FAIL, f"异常: {e}")


async def _scenario_s7(config: Any, port: int, token: str) -> None:
    """过期时间戳报文 → 期望 -32005"""
    try:
        client, loop_task = await _open_client(config, port, token)
        try:
            old_ts = int(time.time()) - 600  # 10 分钟前的报文
            try:
                await client.send_command("core.ping", {"client": "smoke"}, ts=old_ts)
                _record("S7 过期时间戳", _FAIL, "过期 ts 报文被接受！")
            except IpcError as e:
                _record("S7 过期时间戳",
                        _PASS if e.code == STALE_SEQUENCE else _FAIL, f"code={e.code}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S7 过期时间戳", _FAIL, f"异常: {e}")


async def main() -> int:
    config = get_config()
    info = read_daemon_file()
    if info is None:
        print("[错误] daemon 未运行或 run 文件缺失（~/.desk/run/daemon.json）。")
        print("      请先启动 daemon：uv run desk core start")
        return 2

    print("=" * 64)
    print("DeskAgent IPC 安全攻击模拟")
    print("=" * 64)
    print(f"daemon : port={info.port}  pid={info.pid}  version={info.version}")
    print(f"令牌   : ...{info.token[-8:]}  （完整令牌仅存在于 run 文件，不落日志）")
    print(f"TLS    : {'on（证书固定 pinning）' if config.tls else 'off'}")
    print(f"认证   : {'on（core.hello 令牌握手）' if config.auth else 'off'}")
    print(f"证书指纹: {info.cert_sha256 or '(未启用)'}")
    print("-" * 64)

    if not config.auth:
        print("[警告] auth=off：认证门禁已关闭，S1/S2/S5 场景将无法通过（预期行为）。")
    if config.tls and read_cert_pem() is None:
        print("[错误] tls=on 但证书文件缺失（~/.desk/run/desk.crt）。", file=sys.stderr)
        return 2

    await _scenario_s1(config, info.port)
    await _scenario_s2(config, info.port)
    await _scenario_s3(config, info.port)
    await _scenario_s4(config, info.port, info.token)
    await _scenario_s5(config, info.port)
    await _scenario_s6(config, info.port, info.token)
    await _scenario_s7(config, info.port, info.token)

    print("-" * 64)
    passed = sum(1 for _, s, _ in _results if s == _PASS)
    failed = sum(1 for _, s, _ in _results if s == _FAIL)
    skipped = sum(1 for _, s, _ in _results if s == _SKIP)
    print(f"结果: {passed} PASS / {failed} FAIL / {skipped} SKIP")

    # auth=off 属显式关闭，不判失败（警告已在上面给出）
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
