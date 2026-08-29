from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


# 检测 bash 命令是否操作 cwd 之外路径的正则规则列表（强制触发 ASK，不可被 allow_patterns 绕过）
OUTSIDE_CWD_HEURISTICS: list[str] = [
    r"(^|\s)/[^\s]",              # absolute path
    r"(^|\s)~",                   # tilde home
    r"(^|\s)\.\.(/|$|\s)",        # parent traversal
    r"\$\{?HOME\b",               # $HOME variable
    r"\$\{?PWD\b",                # $PWD variable
    r"(^|\s|;|&&|\|\|)cd(\s|$)",  # explicit cd
]

_OUTSIDE_CWD_RE: list[re.Pattern[str]] = [re.compile(p) for p in OUTSIDE_CWD_HEURISTICS]


# 判断 bash 命令是否命中 outside-cwd 启发式规则
def matches_outside_cwd(command: str) -> bool:
    return any(pat.search(command) for pat in _OUTSIDE_CWD_RE)


@dataclass
class ToolPolicy:
    default: PermissionDecision
    allow_patterns: list[str] = field(default_factory=list)
    deny_patterns: list[str] = field(default_factory=list)


@dataclass
class PolicyTrace:
    """策略评估轨迹：决策 + 命中层级 + 命中规则 + 人类可读原因。"""

    decision: PermissionDecision
    tier: str
    matched_rule: str = ""
    reason: str = ""


DEFAULT_POLICIES: dict[str, ToolPolicy] = {
    "bash":       ToolPolicy(default=PermissionDecision.ASK),
    "write_file": ToolPolicy(default=PermissionDecision.ASK),
    "read_file":  ToolPolicy(default=PermissionDecision.ALLOW),
    "read_image": ToolPolicy(default=PermissionDecision.ALLOW),
    "list_dir":   ToolPolicy(default=PermissionDecision.ALLOW),
    "system_query": ToolPolicy(default=PermissionDecision.ALLOW),
    "note_save":  ToolPolicy(default=PermissionDecision.ALLOW),
}

# 未在 DEFAULT_POLICIES 中登记的工具的兜底策略
_UNKNOWN_TOOL_DEFAULT = PermissionDecision.ASK

# bash 参数中展示用的关键字段映射
_PREVIEW_KEY: dict[str, str] = {
    "bash":       "command",
    "read_file":  "path",
    "read_image": "path",
    "write_file": "path",
    "list_dir":   "path",
    "system_query": "category",
    "note_save":  "content",
}
_PREVIEW_MAX = 60


# 为权限审批事件生成人类可读的参数摘要
def param_preview(tool_name: str, params: dict[str, Any]) -> str:
    key = _PREVIEW_KEY.get(tool_name)
    if key and key in params:
        val = str(params[key])
        if len(val) > _PREVIEW_MAX:
            val = val[:_PREVIEW_MAX] + "…"
        return f"{key}={val!r}"
    snippet = str(params)
    return snippet[:_PREVIEW_MAX] if len(snippet) > _PREVIEW_MAX else snippet


# 对工具 + 参数执行 4 层静态策略评估，返回带命中规则的 PolicyTrace
def trace_evaluate(
    tool_name: str,
    params: dict[str, Any],
    policy: ToolPolicy | None = None,
) -> PolicyTrace:
    if policy is None:
        policy = DEFAULT_POLICIES.get(tool_name)

    if policy is None:
        return PolicyTrace(
            decision=_UNKNOWN_TOOL_DEFAULT,
            tier="tier4_default",
            matched_rule="unknown tool",
            reason="工具未登记策略，按默认 ask 处理",
        )

    command = str(params.get("command", "")) if tool_name == "bash" else ""

    # Tier 1: deny_patterns (bash only)
    if command:
        for pat in policy.deny_patterns:
            if re.search(pat, command):
                return PolicyTrace(
                    decision=PermissionDecision.DENY,
                    tier="tier1_deny_pattern",
                    matched_rule=f"deny_pattern: {pat}",
                    reason="命中 deny_patterns 黑名单，直接拒绝",
                )

    # Tier 2: OUTSIDE_CWD_HEURISTICS — forced ASK, not bypassable
    if command and matches_outside_cwd(command):
        return PolicyTrace(
            decision=PermissionDecision.ASK,
            tier="tier2_outside_cwd",
            matched_rule="outside-cwd heuristic",
            reason="bash 命令疑似操作工作目录之外路径，强制询问",
        )

    # Tier 3: allow_patterns (bash only)
    if command:
        for pat in policy.allow_patterns:
            if re.search(pat, command):
                return PolicyTrace(
                    decision=PermissionDecision.ALLOW,
                    tier="tier3_allow_pattern",
                    matched_rule=f"allow_pattern: {pat}",
                    reason="命中 allow_patterns 白名单，自动放行",
                )

    # Tier 4: tool default
    if policy.default == PermissionDecision.ALLOW:
        return PolicyTrace(
            decision=PermissionDecision.ALLOW,
            tier="tier4_default",
            matched_rule="tool default: allow",
            reason="工具默认策略为 allow，自动放行",
        )
    if policy.default == PermissionDecision.DENY:
        return PolicyTrace(
            decision=PermissionDecision.DENY,
            tier="tier4_default",
            matched_rule="tool default: deny",
            reason="工具默认策略为 deny，自动拒绝",
        )
    return PolicyTrace(
        decision=PermissionDecision.ASK,
        tier="tier4_default",
        matched_rule="tool default: ask",
        reason="工具默认策略为 ask，需要人工审批",
    )


# 对工具 + 参数执行静态策略评估，仅返回 ALLOW/DENY/ASK（轨迹见 trace_evaluate）
def evaluate(
    tool_name: str,
    params: dict[str, Any],
    policy: ToolPolicy | None = None,
) -> PermissionDecision:
    return trace_evaluate(tool_name, params, policy).decision
