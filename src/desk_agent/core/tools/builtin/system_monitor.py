from __future__ import annotations

import asyncio
import platform
import time
from datetime import UTC, datetime
from typing import Any, Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field

from desk_agent.core.tools.base import BaseTool, ToolResult

_GPU_QUERY_TIMEOUT = 5.0
_NET_SAMPLE_MAX_S = 3.0
_PROCESS_ATTRS = ("pid", "name", "cpu_percent", "memory_percent")


class SystemMonitorParams(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scope: Literal[
        "overview", "cpu", "memory", "disk", "network", "process", "battery", "gpu", "os"
    ] = "overview"
    # process / gpu 等场景的排序维度（cpu / memory）
    sort_by: Literal["cpu", "memory"] = "cpu"
    # 进程列表只取 top N
    top_n: int = Field(default=10, ge=1, le=50)
    # 仅统计指定磁盘分区（scope=disk 时可选）
    disk_path: str = ""
    # 指定进程 pid（scope=process 时可选）
    pid: int | None = None
    # 网络速率采样时长（秒，scope=network 时生效）
    sample_s: float = Field(default=1.0, ge=0.2, le=_NET_SAMPLE_MAX_S)


def _fmt_bytes(n: int | float) -> str:
    """把字节数格式化为人类可读的 KiB/MiB/GiB 字符串。"""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TiB"


def _pct(value: float) -> str:
    return f"{value:.1f}%"


def _fmt_seconds(seconds: float) -> str:
    """把秒数格式化为 d h m s 形式（用于 uptime / secsleft）。"""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m {secs}s"
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


# ---------- 各 scope 的采集实现（只读、纯函数式，绝不抛出） ----------

def _collect_cpu() -> str:
    lines: list[str] = []
    logical = psutil.cpu_count(logical=True) or 0
    physical = psutil.cpu_count(logical=False) or 0
    lines.append(f"logical cores: {logical}, physical cores: {physical}")
    freq = psutil.cpu_freq()
    if freq is not None:
        lines.append(
            f"frequency: current={freq.current:.0f} MHz"
            f"{f', max={freq.max:.0f} MHz' if freq.max else ''}"
        )
    per_core = psutil.cpu_percent(interval=0.1, percpu=True)
    lines.append(f"per-core usage: {', '.join(_pct(c) for c in per_core)}")
    try:
        load = psutil.getloadavg()
        lines.append(f"load average (1/5/15 min): {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}")
    except (NotImplementedError, OSError):
        lines.append("load average: not available on this platform")
    return "\n".join(lines)


def _collect_memory() -> str:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    lines = [
        f"virtual: total={_fmt_bytes(vm.total)}, used={_fmt_bytes(vm.used)}, "
        f"available={_fmt_bytes(vm.available)}, usage={_pct(vm.percent)}",
        f"swap: total={_fmt_bytes(swap.total)}, used={_fmt_bytes(swap.used)}, "
        f"usage={_pct(swap.percent)}",
    ]
    return "\n".join(lines)


def _collect_disk(disk_path: str = "") -> str:
    lines: list[str] = []
    for part in psutil.disk_partitions(all=False):
        if disk_path and part.mountpoint != disk_path and not part.mountpoint.startswith(disk_path):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            lines.append(
                f"{part.device} ({part.mountpoint}, {part.fstype}): access denied"
            )
            continue
        lines.append(
            f"{part.device} on {part.mountpoint} [{part.fstype}]: "
            f"total={_fmt_bytes(usage.total)}, used={_fmt_bytes(usage.used)}, "
            f"free={_fmt_bytes(usage.free)}, usage={_pct(usage.percent)}"
        )
    if not lines:
        return "no disk partitions found"
    io = psutil.disk_io_counters()
    if io is not None:
        lines.append(
            f"disk io: read={_fmt_bytes(io.read_bytes)}, "
            f"write={_fmt_bytes(io.write_bytes)}"
        )
    return "\n".join(lines)


async def _collect_network(sample_s: float) -> str:
    def _snapshot() -> tuple[int, int, int, int]:
        counters = psutil.net_io_counters()
        if counters is None:
            return (0, 0, 0, 0)
        return (
            counters.bytes_sent,
            counters.bytes_recv,
            counters.packets_sent,
            counters.packets_recv,
        )

    before = _snapshot()
    await asyncio.sleep(sample_s)
    after = _snapshot()

    def _rate(delta: int) -> str:
        return f"{_fmt_bytes(delta / sample_s)}/s"

    lines = [
        "cumulative:",
        f"  sent={_fmt_bytes(after[0])}, recv={_fmt_bytes(after[1])}",
        f"  packets sent={after[2]}, recv={after[3]}",
        f"rates (sampled over {sample_s:g}s):",
        f"  up={_rate(after[0] - before[0])}, down={_rate(after[1] - before[1])}",
    ]
    # 各网卡累计计数（不含速率，避免输出过长）
    for name, counters in psutil.net_io_counters(pernic=True).items():
        if counters is None:
            continue
        lines.append(
            f"  {name}: sent={_fmt_bytes(counters.bytes_sent)}, "
            f"recv={_fmt_bytes(counters.bytes_recv)}"
        )
    return "\n".join(lines)


async def _collect_process(top_n: int, sort_by: str, pid: int | None = None) -> str:
    if pid is not None:
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                pid_val = proc.pid
                name_val = proc.name()
                cpu_val = proc.cpu_percent(interval=0.1)
                mem_val = proc.memory_percent()
                status_val = proc.status()
                rss_val = proc.memory_info().rss
                cmdline_val = " ".join(proc.cmdline())[:200]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
            raise ValueError(f"process {pid}: {exc}") from exc
        lines = [
            f"pid={pid_val}, name={name_val}, status={status_val}",
            f"cpu={_pct(cpu_val)}, mem={_pct(mem_val)}, rss={_fmt_bytes(rss_val)}",
            f"cmdline: {cmdline_val or '(none)'}",
        ]
        return "\n".join(lines)

    # 进程 CPU 百分比是增量指标：先全量预热一次，短采样窗口后再读取
    procs = list(psutil.process_iter(attrs=_PROCESS_ATTRS))
    for proc in procs:
        try:
            proc.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    await asyncio.sleep(0.5)

    processes: list[dict[str, Any]] = []
    for proc in procs:
        try:
            info = proc.info
            cpu = info.get("cpu_percent") or 0.0
            mem = info.get("memory_percent") or 0.0
            processes.append(
                {
                    "pid": info.get("pid"),
                    "name": info.get("name") or "?",
                    "cpu": cpu,
                    "mem": mem,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if sort_by == "memory":
        processes.sort(key=lambda p: p["mem"], reverse=True)
    else:
        processes.sort(key=lambda p: p["cpu"], reverse=True)

    top = processes[:top_n]
    if not top:
        return "no processes found"
    lines = [f"top {len(top)} processes by {sort_by} usage:"]
    for p in top:
        lines.append(
            f"  pid={p['pid']}, name={p['name']}, cpu={_pct(p['cpu'])}, mem={_pct(p['mem'])}"
        )
    return "\n".join(lines)


def _collect_battery() -> str:
    try:
        battery = psutil.sensors_battery()
    except (NotImplementedError, OSError):
        return "battery: not available on this platform"
    if battery is None:
        return "battery: no battery detected (desktop?)"
    plugged = "plugged in" if battery.power_plugged else "on battery"
    secsleft = battery.secsleft
    left = _fmt_seconds(secsleft) if secsleft not in (
        psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED
    ) else "unknown"
    return f"battery: {_pct(battery.percent)}, {plugged}, time left: {left}"


async def _collect_gpu() -> str:
    """通过 nvidia-smi 探测 GPU 状态；不可用时返回提示而非报错。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=_GPU_QUERY_TIMEOUT)
    except (FileNotFoundError, OSError):
        return "gpu: nvidia-smi not found (no NVIDIA GPU or driver not installed)"
    except TimeoutError:
        return "gpu: nvidia-smi timed out"
    if proc.returncode != 0:
        return f"gpu: nvidia-smi exited with code {proc.returncode}"
    lines = ["gpu (nvidia-smi):"]
    for row in stdout_bytes.decode("utf-8", errors="replace").strip().splitlines():
        if row:
            lines.append(f"  {row}")
    return "\n".join(lines) if len(lines) > 1 else "gpu: no GPU found"


def _collect_os() -> str:
    boot = psutil.boot_time()
    boot_str = datetime.fromtimestamp(boot, tz=UTC).isoformat(timespec="seconds")
    return "\n".join(
        [
            f"system: {platform.system()} {platform.release()} ({platform.version()})",
            f"machine: {platform.machine()}, processor: {platform.processor() or 'unknown'}",
            f"hostname: {platform.node()}",
            f"python: {platform.python_version()}",
            f"boot time: {boot_str} (uptime {_fmt_seconds(time.time() - boot)})",
        ]
    )


def _collect_overview() -> str:
    lines: list[str] = []
    lines.append(f"host: {platform.node()} ({platform.system()} {platform.release()})")
    lines.append("-- cpu --")
    lines.append(_collect_cpu())
    lines.append("-- memory --")
    lines.append(_collect_memory())
    lines.append("-- disk (root) --")
    try:
        usage = psutil.disk_usage("/")
        lines.append(
            f"total={_fmt_bytes(usage.total)}, used={_fmt_bytes(usage.used)}, "
            f"free={_fmt_bytes(usage.free)}, usage={_pct(usage.percent)}"
        )
    except (PermissionError, OSError) as exc:
        lines.append(f"root disk access denied: {exc}")
    lines.append("-- battery --")
    lines.append(_collect_battery())
    return "\n".join(lines)


class SystemMonitorTool(BaseTool):
    params_model = SystemMonitorParams
    name = "system_monitor"
    description = (
        "Query local system status (CPU, memory, disk, network, processes, battery, "
        "GPU, OS info). Read-only. Use scope='overview' for a quick summary, or a "
        "specific scope for details: 'cpu', 'memory', 'disk', 'network', 'process', "
        "'battery', 'gpu', 'os'. For processes, sort by cpu/memory and limit with "
        "top_n; pass pid to inspect a single process."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": [
                    "overview", "cpu", "memory", "disk", "network",
                    "process", "battery", "gpu", "os",
                ],
                "description": "What to query (default 'overview').",
            },
            "sort_by": {
                "type": "string",
                "enum": ["cpu", "memory"],
                "description": "Sort processes by cpu or memory (scope='process').",
            },
            "top_n": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Number of top processes to list (default 10).",
            },
            "disk_path": {
                "type": "string",
                "description": "Filter disk partitions by mountpoint (scope='disk').",
            },
            "pid": {
                "type": "integer",
                "description": "Inspect a single process by pid (scope='process').",
            },
            "sample_s": {
                "type": "number",
                "minimum": 0.2,
                "maximum": 3.0,
                "description": "Network sampling duration in seconds (default 1.0).",
            },
        },
        "required": [],
    }

    # 按 scope 分发到各采集实现；任何异常都收敛为 ToolResult 错误，绝不抛出
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        try:
            p = SystemMonitorParams.model_validate(params)
            content = await self._dispatch(p)
        except Exception as exc:  # noqa: BLE001 - 工具边界：异常必须转为 ToolResult
            return ToolResult(
                content=f"system_monitor failed: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(content=content)

    async def _dispatch(self, p: SystemMonitorParams) -> str:
        if p.scope == "cpu":
            return _collect_cpu()
        if p.scope == "memory":
            return _collect_memory()
        if p.scope == "disk":
            return _collect_disk(p.disk_path)
        if p.scope == "network":
            return await _collect_network(p.sample_s)
        if p.scope == "process":
            return await _collect_process(p.top_n, p.sort_by, p.pid)
        if p.scope == "battery":
            return _collect_battery()
        if p.scope == "gpu":
            return await _collect_gpu()
        if p.scope == "os":
            return _collect_os()
        return _collect_overview()
