from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time

import desk_agent
from desk_agent.core.config import DeskConfig
from desk_agent.core.transport import runfile
from desk_agent.core.transport.helpers import connect_authenticated


# 尝试与 daemon 完成一次认证 ping，成功返回，失败抛 ConnectionRefusedError/OSError
async def _ping_check(config: DeskConfig) -> None:
    client = await connect_authenticated(config)
    try:
        await client.send_command(
            "core.ping", {"client": f"cli/{desk_agent.__version__}"}
        )
    finally:
        await client.close()


# 轮询等待 daemon 就绪（run 文件已写入且认证 ping 通过）；
# 就绪过程中的瞬时错误（连接拒绝 / 令牌轮换竞态 / TLS 握手失败）都视为"尚未就绪"
async def _wait_ready(config: DeskConfig, timeout_s: float = 5.0) -> bool:
    from desk_agent.core.transport.socket_client import IpcError

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            await _ping_check(config)
            return True
        except (ConnectionRefusedError, OSError, IpcError):
            await asyncio.sleep(0.25)
    return False


# 打印 daemon 当前状态（running / not running）
def cmd_core_status(config: DeskConfig) -> None:
    info = runfile.read_daemon_file()
    if info is not None and runfile.pid_alive(info.pid):
        print(f"running  pid={info.pid}  port={info.port}")
    else:
        legacy = runfile.running_pid()
        if legacy is not None:
            print(f"running  pid={legacy}")
        else:
            print("not running")


# 在后台启动 daemon，若已在运行则提示并退出；启动后等待就绪
def cmd_core_start(config: DeskConfig) -> None:
    try:
        asyncio.run(_ping_check(config))
        print(f"already running  ({config.host}:{config.port})")
        return
    except (ConnectionRefusedError, OSError):
        pass

    proc = subprocess.Popen(
        [sys.executable, "-m", "desk_agent.core"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"started  pid={proc.pid}")

    if asyncio.run(_wait_ready(config)):
        info = runfile.read_daemon_file()
        port = info.port if info is not None else config.port
        print(f"ready  ({config.host}:{port})  auth={'on' if config.auth else 'off'}  "
              f"tls={'on' if config.tls else 'off'}")
    else:
        print(
            f"warning: daemon 已启动（pid={proc.pid}）但 5s 内未就绪，"
            "请稍后运行 `desk core status` 检查",
            file=sys.stderr,
        )


# 向 daemon 发送 SIGTERM 停止进程，若未运行则提示
def cmd_core_stop(config: DeskConfig) -> None:
    pid = runfile.running_pid()
    if pid is None:
        print("not running")
        return
    os.kill(pid, signal.SIGTERM)
    runfile.remove_daemon_file()
    runfile.remove_legacy_pid_file()
    print(f"stopped  pid={pid}")
