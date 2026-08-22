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
from desk_agent.core.tools.builtin.port import PortQueryParams, PortQueryTool
from desk_agent.core.tools.registry import ToolRegistry


def _free_port() -> int:
    """绑定 127.0.0.1:0 拿到一个空闲端口后立即释放，供查询使用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_free_port_returns_non_error_result() -> None:
    result = await PortQueryTool().invoke({"port": _free_port()})
    assert isinstance(result, ToolResult)
    assert not result.is_error, f"free port query failed: {result.content}"
    assert "free" in result.content


async def test_occupied_port_reports_other_process() -> None:
    """真实子进程占用端口，port_query 应找到 PID 并附 kill 建议。"""
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
    try:
        port = int(child.stdout.readline().strip())
        result = await PortQueryTool().invoke({"port": port})
        assert isinstance(result, ToolResult)
        assert not result.is_error, f"occupied port query failed: {result.content}"
        assert "pid=" in result.content
        assert "LISTEN" in result.content
        assert "kill:" in result.content
        assert "do NOT kill" not in result.content
    finally:
        child.terminate()
        child.wait(timeout=10)


async def test_self_process_is_flagged() -> None:
    """占用端口的是 agent 自身（测试进程）时，输出护栏警告而非 kill 命令。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = int(s.getsockname()[1])
        result = await PortQueryTool().invoke({"port": port})
        assert isinstance(result, ToolResult)
        assert not result.is_error
        assert "do NOT kill" in result.content
        assert "kill:" not in result.content


async def test_invoke_never_raises_on_bad_params() -> None:
    tool = PortQueryTool()
    result = await tool.invoke({"port": "not-a-port"})
    assert isinstance(result, ToolResult)
    assert result.is_error


def test_params_validation() -> None:
    with pytest.raises(ValidationError):
        PortQueryParams.model_validate({"port": 0})
    with pytest.raises(ValidationError):
        PortQueryParams.model_validate({"port": 70000})
    with pytest.raises(ValidationError):
        PortQueryParams.model_validate({"port": 8080, "proto": "icmp"})
    params = PortQueryParams.model_validate({"port": 5173, "proto": "udp"})
    assert params.port == 5173
    assert params.proto == "udp"


def test_registry_registers_tool() -> None:
    registry = ToolRegistry()
    registry.register(PortQueryTool())
    assert registry.get("port_query") is not None
    schemas = registry.tool_schemas()
    assert any(s["name"] == "port_query" for s in schemas)
    assert schemas[0]["input_schema"]["required"] == ["port"]


def test_default_policy_is_allow() -> None:
    policy = DEFAULT_POLICIES["port_query"]
    assert policy.default == PermissionDecision.ALLOW
    assert evaluate("port_query", {"port": 8080}) == PermissionDecision.ALLOW


def test_param_preview_shows_port() -> None:
    assert param_preview("port_query", {"port": 5173}) == "port='5173'"


def test_runner_build_registry_includes_tool() -> None:
    tmp = Path(".tmp-test-port-query")
    tmp.mkdir(exist_ok=True)
    try:
        runner = AgentRunner(config=DeskConfig())
        task_manager = TaskManager(tmp / ".tasks")
        registry = runner._build_registry(task_manager)  # noqa: SLF001 - 测试内部装配
        assert registry.get("port_query") is not None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
