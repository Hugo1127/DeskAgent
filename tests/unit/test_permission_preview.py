"""权限 dry-run 预览单元测试：策略决策链轨迹 + 只读工具副作用预览。

覆盖：
- trace_evaluate 各层级（deny_pattern / outside_cwd / default / unknown tool）
- PermissionManager.preview 反映 session 缓存，且不注册 pending
- preview_tool 的 bash 语法检查（跳过无 bash 环境）、write_file diff（不落盘）、路径元数据
- check_and_wait 重构后行为不变，且 ASK 事件携带 tier / decision_reason
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

import desk_agent.core.permissions.preview as preview_mod
from desk_agent.core.permissions.manager import PermissionManager
from desk_agent.core.permissions.policy import (
    PermissionDecision,
    ToolPolicy,
    evaluate,
    trace_evaluate,
)
from desk_agent.core.permissions.preview import (
    _find_bash,
    _preview_write_file,
    preview_tool,
)


def test_trace_evaluate_default_ask_bash() -> None:
    trace = trace_evaluate("bash", {"command": "ls"})
    assert trace.decision == PermissionDecision.ASK
    assert trace.tier == "tier4_default"
    assert "ask" in trace.reason


def test_trace_evaluate_deny_pattern() -> None:
    policy = ToolPolicy(default=PermissionDecision.ASK, deny_patterns=[r"rm\s+-rf"])
    trace = trace_evaluate("bash", {"command": "rm -rf /tmp/x"}, policy)
    assert trace.decision == PermissionDecision.DENY
    assert trace.tier == "tier1_deny_pattern"
    assert "rm" in trace.matched_rule


def test_trace_evaluate_outside_cwd_forced_ask() -> None:
    trace = trace_evaluate("bash", {"command": "cat /etc/passwd"})
    assert trace.decision == PermissionDecision.ASK
    assert trace.tier == "tier2_outside_cwd"


def test_trace_evaluate_unknown_tool_asks() -> None:
    trace = trace_evaluate("some_mcp_tool", {})
    assert trace.decision == PermissionDecision.ASK
    assert "未登记" in trace.reason


def test_evaluate_wraps_trace() -> None:
    assert evaluate("bash", {"command": "cat /etc/passwd"}) == PermissionDecision.ASK
    assert evaluate("read_file", {"path": "README.md"}) == PermissionDecision.ALLOW


async def test_manager_preview_reflects_session_cache() -> None:
    mgr = PermissionManager()
    # 无缓存：bash 默认 ask
    result = await mgr.preview("toolu_1", "bash", {"command": "echo hi"}, "sess-1")
    assert result["decision"] == "ask"
    assert result["tier"] == "tier6_default"
    assert result["ok"] is True
    assert "命令（不执行" in result["preview_text"]

    # session always_allow 后：预览应反映真实决策链
    mgr._session_always[("sess-1", "bash")] = "allow"
    result = await mgr.preview("toolu_1", "bash", {"command": "echo hi"}, "sess-1")
    assert result["decision"] == "allow"
    assert result["tier"] == "tier3_session_cache"
    # 预览不注册 pending，不影响后续真实审批
    assert mgr._pending == {}


async def test_check_and_wait_auto_allow_unchanged() -> None:
    mgr = PermissionManager()
    called: list[dict] = []

    async def emit(raw: dict) -> None:
        called.append(raw)

    allowed, decision = await mgr.check_and_wait(
        "t1", "read_file", {"path": "x"}, "s", emit
    )
    assert allowed is True
    assert decision == "auto_allow"
    assert called == []


async def test_check_and_wait_ask_emits_trace() -> None:
    mgr = PermissionManager(timeout_s=0)
    events: list[dict] = []

    async def emit(raw: dict) -> None:
        events.append(raw)

    task = asyncio.create_task(
        mgr.check_and_wait("t1", "bash", {"command": "ls"}, "s", emit)
    )
    while not events:
        await asyncio.sleep(0)
    assert events[0]["type"] == "permission.requested"
    assert events[0]["tier"] == "tier6_default"
    assert events[0]["decision_reason"]

    mgr.respond("t1", "deny_once")
    allowed, decision = await task
    assert allowed is False
    assert decision == "deny_once"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
async def test_preview_bash_syntax_check() -> None:
    ok = await preview_tool("bash", {"command": "echo ok"})
    assert "语法检查通过" in ok

    bad = await preview_tool("bash", {"command": "if then"})
    assert "语法错误" in bad


async def test_preview_write_file_diff_without_writing(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("line a\nline b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    text = await preview_tool(
        "write_file",
        {"path": "notes.txt", "content": "line a\nline c\n"},
    )
    assert "变更 diff" in text
    assert "-line b" in text and "+line c" in text
    # 预览绝不落盘
    assert target.read_text(encoding="utf-8") == "line a\nline b\n"


async def test_preview_write_file_new_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    text = await preview_tool(
        "write_file", {"path": "new.txt", "content": "hello"}
    )
    assert "新增文件" in text
    assert not (tmp_path / "new.txt").exists()


async def test_preview_path_stat(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    text = await preview_tool("read_file", {"path": "a.txt"})
    assert "类型: 文件" in text
    assert "1 字节" in text


async def test_preview_path_stat_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    text = await preview_tool("read_file", {"path": "nope.txt"})
    assert "目标不存在" in text


def test_preview_write_file_rejects_traversal() -> None:
    text = _preview_write_file({"path": "../escape.txt", "content": "x"})
    assert "path traversal" in text


def test_preview_write_file_rejects_absolute_path() -> None:
    text = _preview_write_file({"path": str(Path("/etc/escape.txt")), "content": "x"})
    assert "absolute path not allowed" in text


async def test_preview_path_stat_rejects_absolute_path() -> None:
    # 真实工具已拒绝绝对路径，dry-run 预览也应同步拒绝（不泄露元数据）
    text = await preview_tool("read_file", {"path": str(Path("/etc/passwd"))})
    assert "absolute path not allowed" in text


def test_find_bash_env_override(monkeypatch) -> None:
    # DESK_BASH_PATH 指向不存在的文件时，沿探测链回退（无 bash/git 则 None）
    monkeypatch.setenv("DESK_BASH_PATH", "C:/no/such/bash.exe")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(preview_mod, "_GIT_BASH_CANDIDATES", ())
    monkeypatch.setattr(preview_mod, "_GIT_BASH_RELATIVES", ())
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _find_bash() is None


def test_find_bash_via_git_exe_location(monkeypatch, tmp_path: Path) -> None:
    # 非默认安装位置：git.exe 位于 <root>/mingw64/bin，bash 位于 <root>/bin
    root = tmp_path / "Git"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "bash.exe").write_bytes(b"MZ")
    (root / "mingw64" / "bin").mkdir(parents=True)
    git = root / "mingw64" / "bin" / "git.exe"
    git.write_bytes(b"MZ")

    monkeypatch.setenv("DESK_BASH_PATH", "")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(git) if name == "git" else None,
    )
    assert _find_bash() == str(root / "bin" / "bash.exe")


def test_find_bash_via_git_exec_path_fallback(monkeypatch, tmp_path: Path) -> None:
    # git 经 shim 调用（无法从可执行文件推导根目录），回退到 git --exec-path
    root = tmp_path / "Git"
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "bin" / "bash.exe").write_bytes(b"MZ")
    (root / "mingw64" / "libexec" / "git-core").mkdir(parents=True)
    shim = tmp_path / "unrelated" / "git.exe"
    shim.parent.mkdir(parents=True)
    shim.write_bytes(b"MZ")

    class FakeResult:
        returncode = 0
        stdout = str(root / "mingw64" / "libexec" / "git-core")

    monkeypatch.setenv("DESK_BASH_PATH", "")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(shim) if name == "git" else None,
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeResult())
    assert _find_bash() == str(root / "usr" / "bin" / "bash.exe")
