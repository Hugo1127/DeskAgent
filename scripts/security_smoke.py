#!/usr/bin/env python3
"""DeskAgent IPC 安全攻击模拟脚本（红队验收，含实际报文明细）。

用法（需先启动 daemon）：
    uv run desk core start
    uv run python scripts/security_smoke.py          # 默认打印实际报文
    uv run python scripts/security_smoke.py --quiet  # 只显示 PASS/FAIL

脚本模拟以下攻击场景并断言被安全机制拦截：

    S1 无令牌连接直接发命令             → 期望 -32001 UNAUTHENTICATED
    S2 错误令牌握手                     → 期望 -32001 UNAUTHENTICATED
    S3 明文连接（TLS 开启时）           → 期望 TLS 握手失败 / 连接被拒
    S4 正确令牌全链路（ping + 建会话）  → 期望成功（正路径回归）
    S5 未认证伪造 permission.respond    → 期望 -32001（审批通道不可伪造）
    S6 同连接重放相同 seq               → 期望 -32005 STALE_SEQUENCE（防重放）
    S7 过期时间戳报文                   → 期望 -32005 STALE_SEQUENCE（防重放）

报文明细说明：
- 每个场景打印实际写入 TCP 的 NDJSON 请求行、服务端返回的响应行（C→S / S→C）；
- 真实令牌打印时一律替换为 <redacted>，绝不落屏；
- TLS 开启时打印的是 TLS 解密后的应用层 JSON；明文/密文对照见 S3
  （裸 TCP 写明文 → 服务端返回 TLS 协议字节而非 JSON）。

退出码：0 = 全部通过；1 = 存在失败；2 = daemon 未运行 / 环境不满足。

注：本脚本只验证「网络传输 + 协议层」防御；本机同用户恶意进程（可读 run 文件令牌）
不在本脚本覆盖范围内（见 docs/COMMUNICATION_SECURITY.md）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time
import uuid
from typing import Any

import desk_agent
from desk_agent.core.bus.envelope import JsonRpcRequest
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
_QUIET = False
_REAL_TOKEN: str | None = None
_results: list[tuple[str, str, str]] = []


def _record(name: str, status: str, detail: str = "") -> None:
    _results.append((name, status, detail))
    print(f"  [{status:4}] {name}" + (f"  ({detail})" if detail else ""))


def _scenario(n: str, desc: str) -> None:
    """打印场景标题分隔线。"""
    line = f"── {n} {desc}"
    print()
    print(line + " " + "─" * max(1, 62 - len(line)))


# ---------- 报文明细（wire dump） ----------


def _redact_token(obj: Any) -> Any:
    """递归替换报文中的真实令牌（仅打印时生效，不影响实际发送）。"""
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if k == "token" and v == _REAL_TOKEN else _redact_token(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_token(x) for x in obj]
    return obj


def _dump(direction: str, line: bytes, *, note: str = "") -> None:
    """打印一条实际线缆上的报文（JSON 格式化，令牌脱敏）。"""
    if _QUIET:
        return
    print(f"  {direction}  {note}".rstrip())
    try:
        obj = _redact_token(json.loads(line))
        pretty = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pretty = repr(line)
    for ln in pretty.splitlines():
        print("    " + ln)


def _hex_dump(data: bytes, limit: int = 32) -> str:
    return " ".join(f"{b:02x}" for b in data[:limit]) + (" ..." if len(data) > limit else "")


class _WireTap:
    """线缆探针：把实际写入/读到的原始字节转发给打印器（只存在于脚本内）。"""

    def __init__(self, label: str) -> None:
        self.label = label

    def send(self, payload: bytes) -> None:
        _dump("C → S", payload, note=f"[{self.label}] 请求报文（实际写入的 JSON 行）")

    def recv(self, payload: bytes) -> None:
        _dump("S → C", payload, note=f"[{self.label}] 响应/事件报文")


class _TappedSocketClient(SocketClient):
    """脚本内子类：不改动库代码，镜像 send_command 的序列化逻辑以抓取真实字节。

    与 SocketClient.send_command 保持一致的实现（id/seq/ts 自动生成、写 pending 表）；
    若协议层序列化逻辑变化，本脚本会因行为不符而暴露，属预期。
    """

    def __init__(self, *args: Any, tap: _WireTap, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tap = tap

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
        payload = request.model_dump_json().encode() + b"\n"
        self._tap.send(payload)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        self._writer.write(payload)
        await self._writer.drain()
        return await fut

    async def _dispatch(self, line: bytes) -> None:
        self._tap.recv(line)
        await super()._dispatch(line)


# ---------- 连接辅助 ----------


def _new_client(config: Any, port: int, label: str) -> _TappedSocketClient:
    """构建带线缆探针的客户端（TLS 开启时以 run 文件证书为信任锚 pinning）。"""
    ssl_context = client_context(read_cert_pem()) if config.tls else None
    return _TappedSocketClient(
        config.host, port, ssl_context=ssl_context, tap=_WireTap(label)
    )


async def _open_client(
    config: Any,
    port: int,
    token: str,
    label: str,
    *,
    auth: bool = True,
    hello_token: str | None = None,
) -> tuple[SocketClient, asyncio.Task[Any]]:
    client = _new_client(config, port, label)
    await client.connect()
    loop_task = asyncio.create_task(client.run_event_loop())
    if auth:
        await client.authenticate(hello_token or token, client=f"smoke/{desk_agent.__version__}")
    return client, loop_task


async def _close(client: SocketClient, loop_task: asyncio.Task[Any]) -> None:
    if not loop_task.done():
        loop_task.cancel()
    await client.close()


# ---------- 场景 ----------


async def _scenario_s1(config: Any, port: int) -> None:
    """S1 无令牌连接直接发命令 → 期望 -32001"""
    _scenario("S1", "无令牌连接直接发命令（未认证 → 期望 -32001）")
    try:
        client, loop_task = await _open_client(config, port, "", label="S1", auth=False)
        try:
            print("  [动作] 不发送 core.hello，直接调用 core.ping 发命令")
            await client.send_command("core.ping", {"client": "attacker"})
            _record("S1 无令牌连接直接发命令", _FAIL, "未认证请求被接受！")
        except IpcError as e:
            _record("S1 无令牌连接直接发命令",
                    _PASS if e.code == UNAUTHENTICATED else _FAIL,
                    f"code={e.code} message={e}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S1 无令牌连接直接发命令", _FAIL, f"异常: {e}")


async def _scenario_s2(config: Any, port: int) -> None:
    """S2 错误令牌握手 → 期望 -32001"""
    _scenario("S2", "错误令牌握手（core.hello 错令牌 → 期望 -32001）")
    try:
        client = _new_client(config, port, "S2")
        await client.connect()
        loop_task = asyncio.create_task(client.run_event_loop())
        try:
            print("  [动作] 发送 core.hello，令牌为 wrong-token（非真实令牌，可直接展示）")
            await client.authenticate("wrong-token", client="smoke")
            _record("S2 错误令牌握手", _FAIL, "错误令牌被接受！")
        except IpcError as e:
            _record("S2 错误令牌握手",
                    _PASS if e.code == UNAUTHENTICATED else _FAIL,
                    f"code={e.code} message={e}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S2 错误令牌握手", _FAIL, f"异常: {e}")


async def _scenario_s3(config: Any, port: int) -> None:
    """S3 明文连接（TLS 开启时裸 TCP 发 JSON）→ 期望在 TLS 层被拒"""
    _scenario("S3", "明文连接（TLS 开启时裸 TCP 写 JSON → 期望 TLS 层拒绝）")
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
    payload = (json.dumps(req) + "\n").encode()
    print(f"  [动作] 用裸 TCP（不带 TLS）连接 {config.host}:{port}，直接写入明文 JSON")
    if not _QUIET:
        _dump("C → S(明文)", payload, note="[S3] 攻击者试图发送的明文 JSON 行")
    reader, writer = await asyncio.open_connection(config.host, port)
    try:
        writer.write(payload)
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=3.0)
        if not line:
            _record("S3 明文连接", _PASS, "服务端直接关闭连接（EOF），未响应任何明文")
            return
        try:
            json.loads(line)
            _record("S3 明文连接", _FAIL,
                    f"服务端竟然返回了明文 JSON（tls 配置可能未生效？）: {line[:60]!r}")
        except Exception:
            # 返回的是 TLS alert 等二进制协议字节，而不是 JSON
            if not _QUIET:
                print("  S → C(裸socket) 原始字节 hex : " + _hex_dump(line))
                print("  S → C(裸socket) 原始字节 repr: " + repr(line[:64]))
                print("  → 服务端返回的是 TLS 协议字节而非 JSON，明文在 TLS 层即被拒绝（密文信道）")
            _record("S3 明文连接", _PASS,
                    f"服务端返回 TLS 协议字节（非明文 JSON），明文被拒绝: {_hex_dump(line, 8)}")
    except (TimeoutError, ssl.SSLError, ConnectionResetError, BrokenPipeError, OSError) as e:
        _record("S3 明文连接", _PASS, f"TLS 握手失败/连接被拒（{type(e).__name__}）")
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _scenario_s4(config: Any, port: int, token: str) -> None:
    """S4 正确令牌全链路 → 期望成功"""
    _scenario("S4", "正确令牌全链路（hello + ping + session.create → 期望成功）")
    try:
        print("  [动作] 建立连接并发送 core.hello（正确令牌；打印时真实令牌已替换为 <redacted>）")
        client, loop_task = await _open_client(config, port, token, label="S4")
        try:
            pong = await client.send_command(
                "core.ping", {"client": f"smoke/{desk_agent.__version__}"}
            )
            created = await client.send_command("session.create", {"mode": "chat"})
            sid = str(created.get("session_id", "?"))[:8]
            _record("S4 正确令牌全链路", _PASS,
                    f"ping ok, session={sid}, server={pong.get('server_version')}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S4 正确令牌全链路", _FAIL, f"异常: {e}")


async def _scenario_s5(config: Any, port: int) -> None:
    """S5 未认证伪造 permission.respond → 期望 -32001"""
    _scenario("S5", "未认证伪造 permission.respond（→ 期望 -32001）")
    try:
        client, loop_task = await _open_client(config, port, "", label="S5", auth=False)
        try:
            print("  [动作] 未认证连接直接发送 permission.respond 伪造审批")
            await client.send_command(
                "permission.respond",
                {"tool_use_id": "toolu_fake_0001", "decision": "always_allow"},
            )
            _record("S5 伪造权限审批", _FAIL, "未认证的 permission.respond 被接受！")
        except IpcError as e:
            _record("S5 伪造权限审批",
                    _PASS if e.code == UNAUTHENTICATED else _FAIL,
                    f"code={e.code} message={e}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S5 伪造权限审批", _FAIL, f"异常: {e}")


async def _scenario_s6(config: Any, port: int, token: str) -> None:
    """S6 同连接重放相同 seq → 期望 -32005"""
    _scenario("S6", "同连接重放相同 seq（→ 期望 -32005）")
    try:
        client, loop_task = await _open_client(config, port, token, label="S6")
        try:
            print("  [动作] 第一次 core.ping（正常请求，占用当前 seq）")
            await client.send_command("core.ping", {"client": "smoke"})
            print(f"  [动作] 第二次 core.ping 显式复用相同 seq={client._seq} 重放")
            try:
                await client.send_command(  # type: ignore[call-arg]
                    "core.ping", {"client": "smoke"}, seq=client._seq  # type: ignore[attr-defined]
                )
                _record("S6 重放相同 seq", _FAIL, "重复 seq 被接受！")
            except IpcError as e:
                _record("S6 重放相同 seq",
                        _PASS if e.code == STALE_SEQUENCE else _FAIL,
                        f"code={e.code} message={e}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S6 重放相同 seq", _FAIL, f"异常: {e}")


async def _scenario_s7(config: Any, port: int, token: str) -> None:
    """S7 过期时间戳报文 → 期望 -32005"""
    _scenario("S7", "过期时间戳报文（ts 超窗口 → 期望 -32005）")
    try:
        client, loop_task = await _open_client(config, port, token, label="S7")
        try:
            old_ts = int(time.time()) - 600  # 10 分钟前的报文
            print(f"  [动作] core.ping 携带 10 分钟前的 ts={old_ts}（超出 60s 窗口）")
            try:
                await client.send_command("core.ping", {"client": "smoke"}, ts=old_ts)
                _record("S7 过期时间戳", _FAIL, "过期 ts 报文被接受！")
            except IpcError as e:
                _record("S7 过期时间戳",
                        _PASS if e.code == STALE_SEQUENCE else _FAIL,
                        f"code={e.code} message={e}")
        finally:
            await _close(client, loop_task)
    except Exception as e:  # noqa: BLE001
        _record("S7 过期时间戳", _FAIL, f"异常: {e}")


async def main() -> int:
    global _QUIET, _REAL_TOKEN

    parser = argparse.ArgumentParser(
        description="DeskAgent IPC 安全攻击模拟（红队验收，默认打印实际报文明细）"
    )
    parser.add_argument("--quiet", action="store_true", help="不打印报文明细，只输出 PASS/FAIL")
    args = parser.parse_args()
    _QUIET = args.quiet

    config = get_config()
    info = read_daemon_file()
    if info is None:
        print("[错误] daemon 未运行或 run 文件缺失（~/.desk/run/daemon.json）。")
        print("      请先启动 daemon：uv run desk core start（后台静默运行）")
        print("      若你在前台运行 uv run desk-core，请保持其运行，并另开一个终端执行本脚本。")
        return 2
    _REAL_TOKEN = info.token

    print("=" * 70)
    print("DeskAgent IPC 安全攻击模拟" + ("" if _QUIET else "（含实际报文明细）"))
    print("=" * 70)
    print(f"daemon : port={info.port}  pid={info.pid}  version={info.version}")
    print(f"令牌   : ...{info.token[-8:]}  （完整令牌仅存在于 run 文件，打印时一律脱敏）")
    print(f"TLS    : {'on（自签证书 + pinning）' if config.tls else 'off'}")
    print(f"认证   : {'on（core.hello 令牌握手）' if config.auth else 'off'}")
    print(f"证书指纹: {info.cert_sha256 or '(未启用)'}")
    print(f"报文明细: {'打印（--quiet 可关闭）' if not _QUIET else '关闭（--quiet）'}")
    print("-" * 70)
    print("说明: C→S / S→C 为实际写入 TCP 的 NDJSON 行；")
    print("      TLS 开启时，线缆上传输的是 TLS record 密文，这里打印的是解密后的应用层 JSON。")
    print("      明文/密文对照见 S3：裸 TCP 写明文 → 服务端返回 TLS 协议字节而非 JSON。")
    print("-" * 70)

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

    print("-" * 70)
    passed = sum(1 for _, s, _ in _results if s == _PASS)
    failed = sum(1 for _, s, _ in _results if s == _FAIL)
    skipped = sum(1 for _, s, _ in _results if s == _SKIP)
    print(f"结果: {passed} PASS / {failed} FAIL / {skipped} SKIP")

    # auth=off 属显式关闭，不判失败（警告已在上面给出）
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
