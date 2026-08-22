"""统一本地查询工具：system_query。

一个工具、多个实现类别（category），避免为每个查询单独注册一个工具
导致 Agent 工具列表膨胀：
- monitor：系统监控（CPU / 内存 / 磁盘 / 网络 / 进程 / 电池 / GPU / OS）
- port：端口占用（哪个进程占用 TCP/UDP 端口，附 kill 建议）
- software：已安装软件（Windows 注册表 Uninstall 键）

新增查询类别时，在 query/ 包下加实现模块并在本文件注册分发即可，
无需新增独立工具。所有类别都是只读查询，默认权限 ALLOW、零审批打扰。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from desk_agent.core.tools.base import BaseTool, ToolResult
from desk_agent.core.tools.builtin.query.monitor import SystemMonitorParams, collect
from desk_agent.core.tools.builtin.query.port import query as query_port
from desk_agent.core.tools.builtin.query.software import query as query_software

_MAX_RESULTS_DEFAULT = 200
_MAX_RESULTS_LIMIT = 1000


class SystemQueryParams(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # 查询类别：统一工具的分发键
    category: Literal["monitor", "port", "software"] = "monitor"

    # ---- category='monitor' ----
    scope: Literal[
        "overview", "cpu", "memory", "disk", "network", "process", "battery", "gpu", "os"
    ] = "overview"
    sort_by: Literal["cpu", "memory"] = "cpu"
    top_n: int = Field(default=10, ge=1, le=50)
    disk_path: str = ""
    pid: int | None = None
    sample_s: float = Field(default=1.0, ge=0.2, le=3.0)

    # ---- category='port' ----
    port: int | None = Field(default=None, ge=1, le=65535)
    proto: Literal["tcp", "udp"] = "tcp"

    # ---- category='software' ----
    name_filter: str = ""
    include_system_components: bool = False
    max_results: int = Field(default=_MAX_RESULTS_DEFAULT, ge=1, le=_MAX_RESULTS_LIMIT)


class SystemQueryTool(BaseTool):
    params_model = SystemQueryParams
    name = "system_query"
    description = (
        "Query local system information with one tool (read-only, no approval "
        "needed). Pick a category: "
        "'monitor' for system status (scope=overview|cpu|memory|disk|network|"
        "process|battery|gpu|os); "
        "'port' for which process occupies a TCP/UDP port (port=..., proto=...); "
        "'software' for installed applications from the Windows registry "
        "(name_filter=... to narrow, Windows only). Returns human-readable text."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["monitor", "port", "software"],
                "description": "What to query (default 'monitor').",
            },
            "scope": {
                "type": "string",
                "enum": [
                    "overview", "cpu", "memory", "disk", "network",
                    "process", "battery", "gpu", "os",
                ],
                "description": "System aspect to inspect (category='monitor').",
            },
            "sort_by": {
                "type": "string",
                "enum": ["cpu", "memory"],
                "description": "Sort processes by cpu/memory (scope='process').",
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
            "port": {
                "type": "integer",
                "minimum": 1,
                "maximum": 65535,
                "description": "Port number to inspect (category='port').",
            },
            "proto": {
                "type": "string",
                "enum": ["tcp", "udp"],
                "description": "Protocol to inspect, default 'tcp' (category='port').",
            },
            "name_filter": {
                "type": "string",
                "description": "Case-insensitive substring filter on software names.",
            },
            "include_system_components": {
                "type": "boolean",
                "description": "Also list SystemComponent registry entries (category='software').",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_RESULTS_LIMIT,
                "description": "Max software entries to print (default 200).",
            },
        },
        "required": [],
    }

    # 按 category 分发到各实现；任何异常都收敛为 ToolResult 错误，绝不抛出
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        try:
            p = SystemQueryParams.model_validate(params)
            content = await self._dispatch(p)
        except Exception as exc:  # noqa: BLE001 - 工具边界：异常必须转为 ToolResult
            return ToolResult(
                content=f"system_query failed: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(content=content)

    async def _dispatch(self, p: SystemQueryParams) -> str:
        if p.category == "port":
            if p.port is None:
                raise ValueError("category='port' requires 'port' (1-65535)")
            return query_port(p.port, p.proto)
        if p.category == "software":
            return query_software(
                name_filter=p.name_filter,
                include_system_components=p.include_system_components,
                max_results=p.max_results,
            )
        monitor_params = SystemMonitorParams.model_validate(
            {
                "scope": p.scope,
                "sort_by": p.sort_by,
                "top_n": p.top_n,
                "disk_path": p.disk_path,
                "pid": p.pid,
                "sample_s": p.sample_s,
            }
        )
        return await collect(monitor_params)
