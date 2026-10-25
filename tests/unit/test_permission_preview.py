"""权限 dry-run 预览单元测试：策略决策链轨迹 + 只读工具副作用预览。

覆盖：
- trace_evaluate 各层级（deny_pattern / outside_cwd / allow_pattern / default / unknown tool）及 bypassable_by_cache
- PermissionManager.preview 反映 session 缓存，且不注册 pending
- session/global 缓存分级隔离、黑名单（deny_pattern）优先于 toml、outside_cwd 优先于 toml
- preview_tool 的 bash 语法检查（跳过无 bash 环境）、write_file diff（不落盘）、路径元数据
- check_and_wait 重构后行为不变，且 ASK 事件携带 tier / decision_reason
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from desk_agent.core.permissions.manager import PermissionManager
from desk_agent.core.permissions.policy import (
    DEFAULT_POLICIES,
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
    assert trace.bypassable_by_cache is True
    assert trace.forced is False


def test_trace_evaluate_deny_pattern_forces_ask() -> None:
    policy = ToolPolicy(default=PermissionDecision.ASK, deny_patterns=[r"rm\s+-rf"])
    trace = trace_evaluate("bash", {"command": "rm -rf /tmp/x"}, policy)
    assert trace.decision == PermissionDecision.ASK
    assert trace.tier == "tier1_deny_pattern"
    assert "rm" in trace.matched_rule
    assert trace.bypassable_by_cache is False
    assert trace.forced is True


def test_default_bash_deny_patterns_force_ask() -> None:
    # 默认 bash 策略应带跨平台示例黑名单（非空）
    assert DEFAULT_POLICIES["bash"].deny_patterns
    trace = trace_evaluate("bash", {"command": "rm -rf build"})
    assert trace.decision == PermissionDecision.ASK
    assert trace.tier == "tier1_deny_pattern"
    assert trace.bypassable_by_cache is False


def test_trace_evaluate_outside_cwd_forced_ask() -> None:
    trace = trace_evaluate("bash", {"command": "cat /etc/passwd"})
    assert trace.decision == PermissionDecision.ASK
    assert trace.tier == "tier2_outside_cwd"
    assert trace.bypassable_by_cache is False
    assert trace.forced is True


@pytest.mark.parametrize(
    "command",
    [
        "cat /etc/passwd",          # Unix 绝对路径
        "type C:\\secret.txt",      # Windows 盘符绝对路径
        "dir D:/secret",            # Windows 盘符绝对路径（正斜杠）
        "type \\\\server\\share",   # UNC 共享路径
        "cat ~/secret.txt",         # tilde home
        "cat ../secret.txt",        # 父目录穿越（/）
        "type ..\\secret.txt",      # 父目录穿越（\）
        "echo $HOME",               # Unix 环境变量
        "echo %USERPROFILE%",       # Windows 环境变量
        "cd /tmp",                  # 显式 cd 到外部
    ],
)
def test_trace_evaluate_outside_cwd_cross_platform(command: str) -> None:
    trace = trace_evaluate("bash", {"command": command})
    assert trace.decision == PermissionDecision.ASK
    assert trace.tier == "tier2_outside_cwd"
    assert trace.bypassable_by_cache is False


def test_outside_cwd_does_not_flag_date_format() -> None:
    # Windows 环境变量启发式不应把 `date +%H%M%S` 这类格式串误判为越界路径
    trace = trace_evaluate("bash", {"command": "date +%H%M%S"})
    assert trace.tier != "tier2_outside_cwd"


def test_trace_evaluate_unknown_tool_asks() -> None:
    trace = trace_evaluate("some_mcp_tool", {})
    assert trace.decision == PermissionDecision.ASK
    assert "未登记" in trace.reason
    assert trace.bypassable_by_cache is True


def test_trace_evaluate_allow_pattern_tier() -> None:
    policy = ToolPolicy(default=PermissionDecision.ASK, allow_patterns=[r"^echo\b"])
    trace = trace_evaluate("bash", {"command": "echo hi"}, policy)
    assert trace.decision == PermissionDecision.ALLOW
    assert trace.tier == "tier3_allow_pattern"
    assert trace.bypassable_by_cache is True


def test_evaluate_wraps_trace() -> None:
    assert evaluate("bash", {"command": "cat /etc/passwd"}) == PermissionDecision.ASK
    assert evaluate("read_file", {"path": "README.md"}) == PermissionDecision.ALLOW


async def test_manager_preview_reflects_session_cache() -> None:
    mgr = PermissionManager()
    # 无缓存：bash 默认 ask
    result = await mgr.preview("toolu_1", "bash", {"command": "echo hi"}, "sess-1")
    assert result["decision"] == "ask"
    assert result["tier"] == "tier4_default"
    assert result["ok"] is True
    assert "命令（不执行" in result["preview_text"]

    # session_allow 后：预览应反映真实决策链
    mgr._session_cache[("sess-1", "bash")] = "allow"
    result = await mgr.preview("toolu_1", "bash", {"command": "echo hi"}, "sess-1")
    assert result["decision"] == "allow"
    assert result["tier"] == "cache.session"
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
    assert events[0]["tier"] == "tier4_default"
    assert events[0]["decision_reason"]

    mgr.respond("t1", "deny_once")
    allowed, decision = await task
    assert allowed is False
    assert decision == "deny_once"


# ---------- 分级缓存与黑名单优先级 ----------

async def test_barrier_always_global_does_not_write_cache(tmp_path: Path) -> None:
    """安全屏障弹窗上的记忆型决策必须降级为单次决策，绝不写 session / policy.toml。"""
    policy_file = tmp_path / "policy.toml"
    mgr = PermissionManager(policy_file=policy_file)
    events: list[dict] = []

    async def emit(raw: dict) -> None:
        events.append(raw)

    task = asyncio.create_task(
        mgr.check_and_wait(
            "t1", "bash", {"command": "rm -rf build"}, "s1", emit
        )
    )
    while not events:
        await asyncio.sleep(0)
    assert events[0]["forced"] is True
    assert events[0]["tier"] == "tier1_deny_pattern"

    mgr.respond("t1", "always_allow_global")
    allowed, decision = await task
    assert allowed is True
    # 降级为 allow_once，事件/日志不会误报「已全局放行」
    assert decision == "allow_once"
    assert mgr._persistent_always == {}
    assert ("s1", "bash") not in mgr._session_cache
    assert not policy_file.exists()
    # 后续非屏障 bash 仍需审批
    assert (
        mgr._trace("bash", {"command": "echo hi"}, "s1").decision
        == PermissionDecision.ASK
    )


async def test_barrier_session_deny_downgraded_without_cache() -> None:
    mgr = PermissionManager()
    events: list[dict] = []

    async def emit(raw: dict) -> None:
        events.append(raw)

    task = asyncio.create_task(
        mgr.check_and_wait(
            "t1", "bash", {"command": "cat /etc/passwd"}, "s1", emit
        )
    )
    while not events:
        await asyncio.sleep(0)
    mgr.respond("t1", "session_deny")
    allowed, decision = await task
    assert allowed is False
    assert decision == "deny_once"
    assert ("s1", "bash") not in mgr._session_cache


async def test_invalid_decision_falls_back_to_deny_once(tmp_path: Path) -> None:
    policy_file = tmp_path / "policy.toml"
    mgr = PermissionManager(policy_file=policy_file, timeout_s=0)
    events: list[dict] = []

    async def emit(raw: dict) -> None:
        events.append(raw)

    task = asyncio.create_task(
        mgr.check_and_wait("t1", "bash", {"command": "echo hi"}, "s1", emit)
    )
    while not events:
        await asyncio.sleep(0)
    mgr.respond("t1", "allow_everything")
    allowed, decision = await task
    assert allowed is False
    assert decision == "deny_once"
    assert mgr._persistent_always == {}
    assert not policy_file.exists()


async def test_preview_reports_forced_flag() -> None:
    mgr = PermissionManager()
    result = await mgr.preview("t1", "bash", {"command": "cat /etc/passwd"}, "s1")
    assert result["forced"] is True
    assert result["tier"] == "tier2_outside_cwd"

    safe = await mgr.preview("t1", "bash", {"command": "echo hi"}, "s1")
    assert safe["forced"] is False


async def test_release_session_clears_cache_and_pending() -> None:
    mgr = PermissionManager(timeout_s=0)
    mgr._session_cache[("s2", "bash")] = "allow"
    events: list[dict] = []

    async def emit(raw: dict) -> None:
        events.append(raw)

    task = asyncio.create_task(
        mgr.check_and_wait("t1", "bash", {"command": "ls"}, "s1", emit)
    )
    while not events:
        await asyncio.sleep(0)
    mgr._session_cache[("s1", "bash")] = "allow"

    mgr.release_session("s1")
    allowed, decision = await task
    assert allowed is False
    assert decision == "deny_once"
    assert ("s1", "bash") not in mgr._session_cache
    assert ("s2", "bash") in mgr._session_cache


def test_session_allow_does_not_leak_to_other_session() -> None:
    mgr = PermissionManager(
        policies={"bash": ToolPolicy(default=PermissionDecision.ASK)}
    )
    mgr._apply_response("session_allow", "sess-a", "bash")
    a = mgr._trace("bash", {"command": "ls"}, "sess-a")
    b = mgr._trace("bash", {"command": "ls"}, "sess-b")
    assert a.decision == PermissionDecision.ALLOW
    assert a.tier == "cache.session"
    assert b.decision == PermissionDecision.ASK
    assert b.tier == "tier4_default"


def test_session_deny_overrides_global_allow() -> None:
    mgr = PermissionManager(
        policies={"bash": ToolPolicy(default=PermissionDecision.ASK)}
    )
    mgr._persistent_always["bash"] = "allow"
    mgr._apply_response("session_deny", "sess-x", "bash")
    trace = mgr._trace("bash", {"command": "ls"}, "sess-x")
    assert trace.decision == PermissionDecision.DENY
    assert trace.tier == "cache.session"


def test_global_allow_cross_session_and_persists(tmp_path: Path) -> None:
    f = tmp_path / "policy.toml"
    mgr = PermissionManager(policy_file=f)
    mgr._apply_response("always_allow_global", "sess-1", "bash")
    assert mgr._persistent_always["bash"] == "allow"
    # global 决策不写 session 缓存，避免删除 toml 条目后残留
    assert ("sess-1", "bash") not in mgr._session_cache
    # 跨 session 生效
    assert (
        mgr._trace("bash", {"command": "ls"}, "sess-2").tier
        == "cache.global"
    )
    # 重启后从 policy.toml 加载仍生效
    mgr2 = PermissionManager(policy_file=f)
    assert (
        mgr2._trace("bash", {"command": "ls"}, "sess-3").decision
        == PermissionDecision.ALLOW
    )


def test_global_deny_writes_only_persistent(tmp_path: Path) -> None:
    f = tmp_path / "policy.toml"
    mgr = PermissionManager(policy_file=f)
    mgr._apply_response("always_deny_global", "sess-1", "bash")
    assert mgr._persistent_always["bash"] == "deny"
    assert ("sess-1", "bash") not in mgr._session_cache
    assert f.exists()


def test_session_cache_does_not_persist(tmp_path: Path) -> None:
    f = tmp_path / "policy.toml"
    mgr = PermissionManager(policy_file=f)
    mgr._apply_response("session_allow", "sess-1", "bash")
    assert mgr._session_cache[("sess-1", "bash")] == "allow"
    assert mgr._persistent_always == {}
    assert not f.exists()


def test_default_deny_overridden_by_session_allow() -> None:
    mgr = PermissionManager(
        policies={"bash": ToolPolicy(default=PermissionDecision.DENY)}
    )
    assert (
        mgr._trace("bash", {"command": "ls"}, "s").decision
        == PermissionDecision.DENY
    )
    mgr._apply_response("session_allow", "s", "bash")
    trace = mgr._trace("bash", {"command": "ls"}, "s")
    assert trace.decision == PermissionDecision.ALLOW
    assert trace.tier == "cache.session"


def test_deny_pattern_overrides_toml_allow() -> None:
    mgr = PermissionManager(
        policies={
            "bash": ToolPolicy(
                default=PermissionDecision.ASK, deny_patterns=[r"rm\s+-rf"]
            )
        }
    )
    mgr._persistent_always["bash"] = "allow"
    trace = mgr._trace("bash", {"command": "rm -rf /tmp/x"}, "s")
    assert trace.decision == PermissionDecision.ASK
    assert trace.tier == "tier1_deny_pattern"
    assert trace.bypassable_by_cache is False


def test_safe_command_toml_allow() -> None:
    mgr = PermissionManager(
        policies={
            "bash": ToolPolicy(
                default=PermissionDecision.ASK, deny_patterns=[r"rm\s+-rf"]
            )
        }
    )
    mgr._persistent_always["bash"] = "allow"
    trace = mgr._trace("bash", {"command": "echo hi"}, "s")
    assert trace.decision == PermissionDecision.ALLOW
    assert trace.tier == "cache.global"


def test_outside_cwd_overrides_toml_allow() -> None:
    mgr = PermissionManager(
        policies={"bash": ToolPolicy(default=PermissionDecision.ASK)}
    )
    mgr._persistent_always["bash"] = "allow"
    trace = mgr._trace("bash", {"command": "cat /etc/passwd"}, "s")
    assert trace.decision == PermissionDecision.ASK
    assert trace.tier == "tier2_outside_cwd"
    assert trace.bypassable_by_cache is False


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
    # DESK_BASH_PATH 指向不存在的文件、PATH 中无 bash/git 时返回 None
    monkeypatch.setenv("DESK_BASH_PATH", "C:/no/such/bash.exe")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _find_bash() is None


def test_find_bash_via_git_exec_path(monkeypatch, tmp_path: Path) -> None:
    # 仅用 `git --exec-path` 推导 Git 安装根目录（如 D:\Programs\Git 这类非默认位置）
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
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(shim) if name == "git" else None,
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeResult())
    assert _find_bash() == str(root / "usr" / "bin" / "bash.exe")
