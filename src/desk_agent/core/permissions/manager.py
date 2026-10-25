from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desk_agent.core.permissions.policy import (
    ALL_RESPONSE_DECISIONS,
    ALLOW_RESPONSE_DECISIONS,
    DEFAULT_POLICIES,
    MEMORY_RESPONSE_DECISIONS,
    PermissionDecision,
    PolicyTrace,
    ToolPolicy,
    param_preview,
    trace_evaluate,
)
from desk_agent.core.permissions.preview import preview_tool
from desk_agent.core.permissions.storage import load_policy_file, save_policy_file
from desk_agent.core.utils import now_utc as _now

logger = logging.getLogger(__name__)


@dataclass
class _PendingRequest:
    future: asyncio.Future[str]
    session_id: str
    tool_name: str


# 管理工具调用权限：策略评估、用户审批挂起、session 级与持久化缓存、超时
class PermissionManager:
    def __init__(
        self,
        policies: dict[str, ToolPolicy] | None = None,
        *,
        policy_file: Path | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._policies: dict[str, ToolPolicy] = policies or dict(DEFAULT_POLICIES)
        # tool_use_id → pending Future + metadata
        self._pending: dict[str, _PendingRequest] = {}
        # (session_id, tool_name) → "allow" | "deny"（session 内存，重启丢失）
        self._session_cache: dict[tuple[str, str], str] = {}
        # tool_name → "allow" | "deny"（持久化，从 policy_file 加载）
        self._policy_file = policy_file
        self._persistent_always: dict[str, str] = (
            load_policy_file(policy_file) if policy_file is not None else {}
        )
        # 0 表示不超时
        self._timeout_s = timeout_s

    # 复现 check_and_wait 的完整决策链（含 session/持久化缓存），返回带命中规则的轨迹。
    # 静态评估只复用 trace_evaluate 一处；安全屏障（bypassable_by_cache=False）不可被缓存绕过。
    def _trace(
        self,
        tool_name: str,
        params: dict[str, Any],
        session_id: str,
    ) -> PolicyTrace:
        policy = self._policies.get(tool_name)
        result = trace_evaluate(tool_name, params, policy)

        # 安全屏障：deny_pattern(ASK) 和 outside_cwd(ASK) 在此拦截，
        # 不可被 session/global 缓存绕过。default=DENY 的 bypassable=True 不在此拦截。
        if not result.bypassable_by_cache:
            return result

        # Tier 3: session 级缓存（内存，仅当前 session）
        session_key = (session_id, tool_name)
        if session_key in self._session_cache:
            cached = self._session_cache[session_key]
            return PolicyTrace(
                decision=(
                    PermissionDecision.ALLOW
                    if cached == "allow" else PermissionDecision.DENY
                ),
                tier="cache.session",
                matched_rule=f"session cache: {cached}",
                reason=f"本 session 已有 session_{cached} 决策缓存",
                bypassable_by_cache=True,
            )

        # Tier 4: global 级缓存（policy.toml）
        if tool_name in self._persistent_always:
            cached = self._persistent_always[tool_name]
            return PolicyTrace(
                decision=(
                    PermissionDecision.ALLOW
                    if cached == "allow" else PermissionDecision.DENY
                ),
                tier="cache.global",
                matched_rule=f"policy.toml always: {cached}",
                reason=f"policy.toml 已持久化全局 {cached} 决策",
                bypassable_by_cache=True,
            )

        return result

    # 检查权限；如需 ask 则向客户端发事件并等待响应；返回 (allowed, decision_str)
    async def check_and_wait(
        self,
        tool_use_id: str,
        tool_name: str,
        params: dict[str, Any],
        session_id: str,
        event_emitter: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> tuple[bool, str]:
        trace = self._trace(tool_name, params, session_id)
        if trace.decision == PermissionDecision.ALLOW:
            logger.debug(
                "permission: auto allow tool=%s tier=%s", tool_name, trace.tier
            )
            return True, "auto_allow"
        if trace.decision == PermissionDecision.DENY:
            logger.debug(
                "permission: auto deny tool=%s tier=%s", tool_name, trace.tier
            )
            return False, "auto_deny"

        # ASK 路径（来自 deny_pattern / outside_cwd 强制 ASK，或 default=ASK）
        loop = asyncio.get_event_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[tool_use_id] = _PendingRequest(
            future=future,
            session_id=session_id,
            tool_name=tool_name,
        )

        await event_emitter(
            {
                "type": "permission.requested",
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "params": params,
                "param_preview": param_preview(tool_name, params),
                "session_id": session_id,
                "ts": _now(),
                "tier": trace.tier,
                "forced": trace.forced,
                "decision_reason": trace.reason,
            }
        )

        try:
            if self._timeout_s > 0:
                raw = await asyncio.wait_for(future, timeout=self._timeout_s)
            else:
                raw = await future
        except TimeoutError:
            self._pending.pop(tool_use_id, None)
            logger.info("permission: timeout tool_use_id=%s tool=%s", tool_use_id, tool_name)
            return False, "timeout"

        allowed, effective = self._apply_response(
            raw,
            session_id,
            tool_name,
            memory_allowed=trace.bypassable_by_cache,
        )
        return allowed, effective

    # dry-run 预览：完整复现决策链 + 只读工具副作用预览。
    # 不注册 pending、不写 policy.toml、不产生任何事件。
    async def preview(
        self,
        tool_use_id: str,
        tool_name: str,
        params: dict[str, Any],
        session_id: str = "",
    ) -> dict[str, Any]:
        trace = self._trace(tool_name, params, session_id)
        preview_text = await preview_tool(tool_name, params)
        return {
            "ok": True,
            "tool_use_id": tool_use_id,
            "decision": trace.decision.value,
            "tier": trace.tier,
            "matched_rule": trace.matched_rule,
            "reason": trace.reason,
            "forced": trace.forced,
            "preview_text": preview_text,
            "safe": True,
        }

    # 处理客户端返回的审批决策，resolve 对应 Future
    def respond(self, tool_use_id: str, decision: str) -> None:
        req = self._pending.pop(tool_use_id, None)
        if req is None:
            logger.warning("permission.respond: unknown tool_use_id=%s", tool_use_id)
            return
        if not req.future.done():
            req.future.set_result(decision)

    # 应用审批决策，分级写入缓存，返回 (是否放行, 实际生效的决策)。
    # allow_once/deny_once 不写缓存；session_allow/session_deny 只写 session；
    # always_allow_global/always_deny_global 只写 persistent + 落盘
    # （不写 session，避免删除 toml 条目后残留）。
    # memory_allowed=False（命中安全屏障）时，记忆型决策降级为单次决策且不写任何缓存，
    # 防止「在屏障弹窗上点 session/global 记住」把整个工具（例如 bash）预授权给后续非屏障调用。
    def _apply_response(
        self,
        decision: str,
        session_id: str,
        tool_name: str,
        *,
        memory_allowed: bool = True,
    ) -> tuple[bool, str]:
        if decision not in ALL_RESPONSE_DECISIONS:
            logger.error(
                "permission: invalid decision=%r tool=%s, treating as deny_once",
                decision, tool_name,
            )
            return False, "deny_once"

        allow = decision in ALLOW_RESPONSE_DECISIONS

        if decision in MEMORY_RESPONSE_DECISIONS and not memory_allowed:
            effective = "allow_once" if allow else "deny_once"
            logger.warning(
                "permission: barrier blocks cache write decision=%s tool=%s; "
                "downgraded to %s",
                decision, tool_name, effective,
            )
            return allow, effective

        if decision in ("session_allow", "session_deny"):
            verdict = "allow" if decision == "session_allow" else "deny"
            self._session_cache[(session_id, tool_name)] = verdict
        elif decision in ("always_allow_global", "always_deny_global"):
            verdict = "allow" if decision == "always_allow_global" else "deny"
            self._persistent_always[tool_name] = verdict
            if self._policy_file is not None:
                try:
                    save_policy_file(self._persistent_always, self._policy_file)
                    logger.info("permission: policy.toml written path=%s", self._policy_file)
                except Exception:
                    logger.exception(
                        "permission: failed to write policy.toml path=%s",
                        self._policy_file,
                    )
            else:
                logger.warning("permission: policy_file is None, skipping persistence")
        return allow, decision

    # 客户端断连时拒绝该 session 所有待审批请求，防止 Future 永久挂起
    def cancel_session(self, session_id: str, reason: str = "client_disconnected") -> None:
        to_cancel = [
            uid for uid, req in self._pending.items()
            if req.session_id == session_id
        ]
        for uid in to_cancel:
            req = self._pending.pop(uid)
            if not req.future.done():
                logger.debug(
                    "permission: cancel pending tool_use_id=%s reason=%s", uid, reason
                )
                req.future.set_result("deny_once")

    # session 关闭时回收该会话的 session 级缓存与挂起请求，避免长驻 daemon 内存增长
    def release_session(self, session_id: str) -> None:
        self.cancel_session(session_id, reason="session_closed")
        stale = [key for key in self._session_cache if key[0] == session_id]
        for key in stale:
            self._session_cache.pop(key, None)
        if stale:
            logger.debug(
                "permission: released %d session cache entries for session=%s",
                len(stale), session_id,
            )
