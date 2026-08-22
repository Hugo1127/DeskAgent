"""端口占用查询实现：system_query 工具的 port 类别。

只读查询本机 TCP/UDP 端口被哪个进程占用，并给出当前平台上可直接
执行的 kill 命令（配合 bash 工具释放端口）。纯查询，绝不写系统。

本模块只提供实现（query + 辅助函数），不定义 BaseTool——
统一入口在 query/tool.py 的 SystemQueryTool。
"""

from __future__ import annotations

import os
import platform

import psutil

_CMDLINE_MAX = 200


def _connections_on_port(port: int, proto: str) -> list[tuple[int | None, str, str]]:
    """枚举本机 proto 连接中 laddr 命中 port 的连接，返回 (pid, status, local)。"""
    try:
        net_conns = psutil.net_connections(kind=proto)
    except (psutil.AccessDenied, OSError) as exc:
        raise ValueError(
            f"cannot enumerate {proto} connections: {exc}"
            " (on Windows, run with administrator rights for full visibility)"
        ) from exc
    conns: list[tuple[int | None, str, str]] = []
    for c in net_conns:
        if not c.laddr or c.laddr.port != port:
            continue
        conns.append((c.pid, c.status or "", f"{c.laddr.ip}:{c.laddr.port}"))
    return conns


def _process_info(pid: int) -> dict[str, str]:
    """查询进程名 / 命令行；进程已退出或无权访问时返回占位符。"""
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            name = proc.name()
            cmdline = " ".join(proc.cmdline())[:_CMDLINE_MAX]
            status = proc.status()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return {"name": "?", "cmdline": "", "status": "?"}
    return {"name": name, "cmdline": cmdline, "status": status}


def _is_self_process(pid: int) -> bool:
    """pid 是否为当前 daemon 自身或其祖先（杀它们会连坐杀掉 agent 自己）。"""
    try:
        current = os.getpid()
    except OSError:
        return False
    if pid == current:
        return True
    try:
        proc: psutil.Process | None = psutil.Process(current)
        while proc is not None:
            if proc.pid == pid:
                return True
            proc = proc.parent()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
    return False


def _kill_command(pid: int) -> str:
    """给出当前平台上可直接执行的杀进程命令，供模型配合 bash 工具使用。"""
    if platform.system() == "Windows":
        return f"taskkill /PID {pid} /T /F"
    return f"kill {pid}   # 若未退出再执行: kill -9 {pid}"


# 查询端口占用并格式化为人类可读文本；proto 非法时抛 ValueError（由工具层收敛）
def query(port: int, proto: str = "tcp") -> str:
    if proto not in ("tcp", "udp"):
        raise ValueError(f"unsupported proto: {proto!r} (only 'tcp'/'udp')")
    conns = _connections_on_port(port, proto)
    if not conns:
        return f"port {port} ({proto}): free — no process found"

    # 按 pid 去重（同一进程可能有多个连接），LISTEN 优先展示
    seen: set[int | None] = set()
    entries: list[tuple[int | None, str, str]] = []
    for pid, status, local in sorted(conns, key=lambda c: c[1] != "LISTEN"):
        if pid in seen:
            continue
        seen.add(pid)
        entries.append((pid, status, local))

    lines = [f"port {port} ({proto}): {len(entries)} process(es) occupying"]
    for pid, status, local in entries:
        if pid is not None:
            info = _process_info(pid)
        else:
            info = {"name": "?", "cmdline": "", "status": "?"}
        lines.append(
            f"  pid={pid}  name={info['name']}  status={status}  local={local}"
        )
        if info["cmdline"]:
            lines.append(f"    cmdline: {info['cmdline']}")
        if pid is None:
            lines.append("    (kernel/System-owned connection — cannot be killed by user)")
        elif _is_self_process(pid):
            lines.append("    ⚠ this is the DeskAgent daemon process itself — do NOT kill it")
        else:
            lines.append(f"    kill: {_kill_command(pid)}")
    return "\n".join(lines)
