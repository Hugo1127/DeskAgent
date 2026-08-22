from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field

from desk_agent.core.tools.base import BaseTool, ToolResult

_CMDLINE_MAX = 200


class PortQueryParams(BaseModel):
    model_config = ConfigDict(extra="ignore")

    port: int = Field(ge=1, le=65535)
    # 只查 tcp / udp，避免误报（unix socket 等无端口概念）
    proto: Literal["tcp", "udp"] = "tcp"


@dataclass
class _Conn:
    pid: int | None
    status: str
    local: str


# ---------- 纯函数查询实现（只读，绝不抛出） ----------

def _connections_on_port(port: int, proto: str) -> list[_Conn]:
    """枚举本机 proto 连接中 laddr 命中 port 的连接。"""
    try:
        net_conns = psutil.net_connections(kind=proto)
    except (psutil.AccessDenied, OSError) as exc:
        raise ValueError(
            f"cannot enumerate {proto} connections: {exc}"
            " (on Windows, run with administrator rights for full visibility)"
        ) from exc
    conns: list[_Conn] = []
    for c in net_conns:
        if not c.laddr or c.laddr.port != port:
            continue
        conns.append(_Conn(pid=c.pid, status=c.status or "", local=f"{c.laddr.ip}:{c.laddr.port}"))
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


def _format_query(port: int, proto: str) -> str:
    conns = _connections_on_port(port, proto)
    if not conns:
        return f"port {port} ({proto}): free — no process found"

    # 按 pid 去重（同一进程可能有多个连接），LISTEN 优先展示
    seen: set[int | None] = set()
    entries: list[_Conn] = []
    for conn in sorted(conns, key=lambda c: c.status != "LISTEN"):
        if conn.pid in seen:
            continue
        seen.add(conn.pid)
        entries.append(conn)

    lines = [f"port {port} ({proto}): {len(entries)} process(es) occupying"]
    for conn in entries:
        if conn.pid is not None:
            info = _process_info(conn.pid)
        else:
            info = {"name": "?", "cmdline": "", "status": "?"}
        lines.append(
            f"  pid={conn.pid}  name={info['name']}  status={conn.status}  "
            f"local={conn.local}"
        )
        if info["cmdline"]:
            lines.append(f"    cmdline: {info['cmdline']}")
        if conn.pid is None:
            lines.append("    (kernel/System-owned connection — cannot be killed by user)")
        elif _is_self_process(conn.pid):
            lines.append("    ⚠ this is the DeskAgent daemon process itself — do NOT kill it")
        else:
            lines.append(f"    kill: {_kill_command(conn.pid)}")
    return "\n".join(lines)


class PortQueryTool(BaseTool):
    params_model = PortQueryParams
    name = "port_query"
    description = (
        "Find which process is occupying a TCP/UDP port (read-only, safe). "
        "Use this when starting a dev server reports 'port already in use'. "
        "Returns pid, process name, status and a ready-to-run kill command for "
        "this platform. To free the port, run that kill command via the bash "
        "tool (bash will ask you to approve it)."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "port": {
                "type": "integer",
                "minimum": 1,
                "maximum": 65535,
                "description": "Port number to inspect.",
            },
            "proto": {
                "type": "string",
                "enum": ["tcp", "udp"],
                "description": "Protocol to inspect (default 'tcp').",
            },
        },
        "required": ["port"],
    }

    # 查询端口占用；任何异常都收敛为 ToolResult 错误，绝不抛出
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        try:
            p = PortQueryParams.model_validate(params)
            content = _format_query(p.port, p.proto)
        except Exception as exc:  # noqa: BLE001 - 工具边界：异常必须转为 ToolResult
            return ToolResult(
                content=f"port_query failed: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(content=content)
