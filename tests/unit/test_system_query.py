from __future__ import annotations

import shutil
import socket
import subprocess
import sys
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
from desk_agent.core.tools.builtin.query.tool import SystemQueryParams, SystemQueryTool
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


# ---------- category='monitor' ----------

@pytest.mark.parametrize("scope", ALL_SCOPES)
async def test_monitor_scope_returns_non_error_result(scope: str) -> None:
    result = await SystemQueryTool().invoke({"category": "monitor", "scope": scope})
    assert isinstance(result, ToolResult)
    assert result.content, f"scope={scope} returned empty content"
    assert not result.is_error, f"scope={scope} failed: {result.content}"


async def test_monitor_overview_contains_sections() -> None:
    result = await SystemQueryTool().invoke({"category": "monitor", "scope": "overview"})
    assert "-- cpu --" in result.content
    assert "-- memory --" in result.content
    assert "-- disk" in result.content


async def test_monitor_process_pid_unknown_is_error_result() -> None:
    result = await SystemQueryTool().invoke(
        {"category": "monitor", "scope": "process", "pid": 999_999_999}
    )
    assert result.is_error
    assert "999999999" in result.content


async def test_monitor_process_list_respects_top_n() -> None:
    result = await SystemQueryTool().invoke(
        {"category": "monitor", "scope": "process", "top_n": 5, "sort_by": "memory"}
    )
    assert not result.is_error
    lines = [ln for ln in result.content.splitlines() if ln.startswith("  pid=")]
    assert 1 <= len(lines) <= 5


# ---------- category='port' ----------

def _free_port() -> int:
    """绑定 127.0.0.1:0 拿到一个空闲端口后立即释放，供查询使用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_port_free_port_returns_non_error_result() -> None:
    result = await SystemQueryTool().invoke(
        {"category": "port", "port": _free_port()}
    )
    assert isinstance(result, ToolResult)
    assert not result.is_error, f"free port query failed: {result.content}"
    assert "free" in result.content


async def test_port_occupied_reports_other_process() -> None:
    """真实子进程占用端口，system_query 应找到 PID 并附 kill 建议。"""
    child_code = (
        "import socket, time\n"
        "s = socket.socket()\n"
        "s.bind(('127.0.0.1', 0))\n"
        "s.listen(1)\n"
        "print(s.getsockname()[1], flush=True)\n"
        "time.sleep(60)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert child.stdout is not None
    try:
        port = int(child.stdout.readline().strip())
        result = await SystemQueryTool().invoke(
            {"category": "port", "port": port}
        )
        assert isinstance(result, ToolResult)
        assert not result.is_error, f"occupied port query failed: {result.content}"
        assert "pid=" in result.content
        assert "LISTEN" in result.content
        assert "kill:" in result.content
        assert "do NOT kill" not in result.content
    finally:
        child.terminate()
        child.wait(timeout=10)


async def test_port_self_process_is_flagged() -> None:
    """占用端口的是 agent 自身（测试进程）时，输出护栏警告而非 kill 命令。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = int(s.getsockname()[1])
        result = await SystemQueryTool().invoke(
            {"category": "port", "port": port}
        )
        assert isinstance(result, ToolResult)
        assert not result.is_error
        assert "do NOT kill" in result.content
        assert "kill:" not in result.content


# ---------- category='software' ----------

async def test_software_never_raises() -> None:
    """Windows 上真实查询注册表；其他平台应返回提示文本而不是报错。"""
    result = await SystemQueryTool().invoke(
        {"category": "software", "name_filter": "", "max_results": 20}
    )
    assert isinstance(result, ToolResult)
    assert not result.is_error, f"software query failed: {result.content}"
    assert "installed software" in result.content


# ---------- 参数校验 / 注册 / 权限 ----------

async def test_invoke_never_raises_on_bad_params() -> None:
    tool = SystemQueryTool()
    result = await tool.invoke({"category": "not-a-category"})
    assert isinstance(result, ToolResult)
    assert result.is_error
    # category='port' 缺少 port
    result = await tool.invoke({"category": "port"})
    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "port" in result.content
    # 类型错误
    result = await tool.invoke({"port": "not-a-port"})
    assert isinstance(result, ToolResult)
    assert result.is_error


def test_params_validation() -> None:
    with pytest.raises(ValidationError):
        SystemQueryParams.model_validate({"category": "nope"})
    with pytest.raises(ValidationError):
        SystemQueryParams.model_validate({"scope": "nope"})
    with pytest.raises(ValidationError):
        SystemQueryParams.model_validate({"top_n": 0})
    with pytest.raises(ValidationError):
        SystemQueryParams.model_validate({"sample_s": 10.0})
    with pytest.raises(ValidationError):
        SystemQueryParams.model_validate({"port": 0})
    with pytest.raises(ValidationError):
        SystemQueryParams.model_validate({"port": 70000})
    with pytest.raises(ValidationError):
        SystemQueryParams.model_validate({"port": 8080, "proto": "icmp"})
    with pytest.raises(ValidationError):
        SystemQueryParams.model_validate({"max_results": 5000})
    params = SystemQueryParams.model_validate(
        {"category": "software", "name_filter": "chrome", "max_results": 50}
    )
    assert params.category == "software"
    assert params.name_filter == "chrome"
    assert params.max_results == 50


def test_registry_registers_only_unified_tool() -> None:
    registry = ToolRegistry()
    registry.register(SystemQueryTool())
    assert registry.get("system_query") is not None
    schemas = registry.tool_schemas()
    names = {s["name"] for s in schemas}
    assert "system_query" in names
    # 旧的独立工具已整合，不应再出现在工具列表中
    assert "system_monitor" not in names
    assert "port_query" not in names
    system_query_schema = next(s for s in schemas if s["name"] == "system_query")
    input_schema = system_query_schema["input_schema"]
    assert isinstance(input_schema, dict)
    assert input_schema["required"] == []


def test_default_policy_is_allow() -> None:
    policy = DEFAULT_POLICIES["system_query"]
    assert policy.default == PermissionDecision.ALLOW
    assert evaluate("system_query", {"category": "monitor", "scope": "cpu"}) == PermissionDecision.ALLOW
    assert evaluate("system_query", {"category": "port", "port": 8080}) == PermissionDecision.ALLOW
    assert evaluate("system_query", {"category": "software", "name_filter": "chrome"}) == PermissionDecision.ALLOW


def test_param_preview_shows_category() -> None:
    assert param_preview("system_query", {"category": "port", "port": 5173}) == "category='port'"


def test_runner_build_registry_includes_only_unified_tool() -> None:
    # 沙箱下 tempfile 的 0o700 目录不可枚举，这里用默认权限的临时目录
    tmp = Path(".tmp-test-system-query")
    tmp.mkdir(exist_ok=True)
    try:
        runner = AgentRunner(config=DeskConfig())
        task_manager = TaskManager(tmp / ".tasks")
        registry = runner._build_registry(task_manager)  # noqa: SLF001 - 测试内部装配
        assert registry.get("system_query") is not None
        assert registry.get("system_monitor") is None
        assert registry.get("port_query") is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
