from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from desk_agent.core.config import DeskConfig
from desk_agent.core.permissions.policy import (
    DEFAULT_POLICIES,
    PermissionDecision,
    evaluate,
    param_preview,
)
from desk_agent.core.runner import AgentRunner
from desk_agent.core.task.manager import TaskManager
from desk_agent.core.tools.base import ToolResult
from desk_agent.core.tools.builtin.system_monitor import (
    SystemMonitorParams,
    SystemMonitorTool,
)
from desk_agent.core.tools.registry import ToolRegistry

ALL_SCOPES = [
    "overview",
    "cpu",
    "memory",
    "disk",
    "network",
    "process",
    "battery",
    "gpu",
    "os",
]


@pytest.mark.parametrize("scope", ALL_SCOPES)
async def test_scope_returns_non_error_result(scope: str) -> None:
    tool = SystemMonitorTool()
    result = await tool.invoke({"scope": scope})
    assert isinstance(result, ToolResult)
    assert result.content, f"scope={scope} returned empty content"
    assert not result.is_error, f"scope={scope} failed: {result.content}"


async def test_overview_contains_sections() -> None:
    result = await SystemMonitorTool().invoke({"scope": "overview"})
    assert "-- cpu --" in result.content
    assert "-- memory --" in result.content
    assert "-- disk" in result.content


async def test_process_pid_unknown_is_error_result() -> None:
    result = await SystemMonitorTool().invoke({"scope": "process", "pid": 999_999_999})
    assert result.is_error
    assert "999999999" in result.content


async def test_process_list_respects_top_n() -> None:
    result = await SystemMonitorTool().invoke(
        {"scope": "process", "top_n": 5, "sort_by": "memory"}
    )
    assert not result.is_error
    lines = [ln for ln in result.content.splitlines() if ln.startswith("  pid=")]
    assert 1 <= len(lines) <= 5


async def test_invoke_never_raises_on_bad_params() -> None:
    tool = SystemMonitorTool()
    result = await tool.invoke({"scope": "not-a-scope"})
    assert isinstance(result, ToolResult)
    assert result.is_error


def test_params_validation() -> None:
    with pytest.raises(ValidationError):
        SystemMonitorParams.model_validate({"scope": "nope"})
    with pytest.raises(ValidationError):
        SystemMonitorParams.model_validate({"top_n": 0})
    with pytest.raises(ValidationError):
        SystemMonitorParams.model_validate({"sample_s": 10.0})
    params = SystemMonitorParams.model_validate({"scope": "cpu", "top_n": 3})
    assert params.scope == "cpu"
    assert params.top_n == 3


def test_registry_registers_tool() -> None:
    registry = ToolRegistry()
    registry.register(SystemMonitorTool())
    assert registry.get("system_monitor") is not None
    schemas = registry.tool_schemas()
    assert any(s["name"] == "system_monitor" for s in schemas)


def test_default_policy_is_allow() -> None:
    policy = DEFAULT_POLICIES["system_monitor"]
    assert policy.default == PermissionDecision.ALLOW
    assert evaluate("system_monitor", {"scope": "cpu"}) == PermissionDecision.ALLOW


def test_param_preview_shows_scope() -> None:
    assert param_preview("system_monitor", {"scope": "gpu"}) == "scope='gpu'"


def test_runner_build_registry_includes_tool() -> None:
    # 沙箱下 tempfile 的 0o700 目录不可枚举，这里用默认权限的临时目录
    tmp = Path(".tmp-test-system-monitor")
    tmp.mkdir(exist_ok=True)
    try:
        runner = AgentRunner(config=DeskConfig())
        task_manager = TaskManager(tmp / ".tasks")
        registry = runner._build_registry(task_manager)  # noqa: SLF001 - 测试内部装配
        assert registry.get("system_monitor") is not None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
