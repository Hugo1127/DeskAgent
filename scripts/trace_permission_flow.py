#!/usr/bin/env python3
"""
DeskAgent 端到端权限审批流程诊断脚本

模拟 TUI 的完整链路：
  1. 连接 desk-core（读 run 文件 → TLS 证书固定 → core.hello 令牌认证）
  2. 订阅事件，创建 session，发消息触发 bash 工具（bash 总是需要审批）
  3. 收到 permission.requested → 自动回复 allow_once
  4. 等待 run.finished，打印全部事件日志

注意：脚本复用 CLI/TUI 统一的 connect_authenticated，协议要求
core.hello 握手 + 每条请求带 seq/ts（防重放），手动拼 JSON 的老写法已不可用。

前置条件：daemon 已启动（`desk core start`，或 `python -m desk_agent.core`）。

运行：uv run python scripts/trace_permission_flow.py
"""
from __future__ import annotations

import asyncio
from typing import Any

from desk_agent.core.config import get_config
from desk_agent.core.transport.helpers import connect_authenticated

GOAL = "用 bash 执行 `echo hello_permission_test`，把结果告诉我"


async def main() -> None:
    config = get_config()
    client = await connect_authenticated(config)
    print("[connect] authenticated (TLS + token)")

    events_log: list[dict[str, Any]] = []
    permission_event: dict[str, Any] | None = None
    finished = asyncio.Event()

    # ── 事件回调（连接读循环内串行调用） ───────────────────────────────
    async def on_event(event: dict[str, Any]) -> None:
        nonlocal permission_event
        t = event.get("type", "?")
        events_log.append(event)
        print(f"  [event] {t:35s}  {_brief(event)}")

        if t == "permission.requested":
            permission_event = event
            tool_use_id = event["tool_use_id"]
            print(f"\n  *** 收到 permission.requested  tool={event['tool_name']}")
            print(f"      param_preview={event.get('param_preview')!r}")
            print("      → 自动回复 allow_once\n")
            # 不能在这里 await send_command：事件回调阻塞读循环，响应永远读不到 → 死锁。
            # 派发独立任务回复，读循环保持空闲继续消费后续事件。
            respond_task = asyncio.create_task(
                client.send_command(
                    "permission.respond",
                    {"tool_use_id": tool_use_id, "decision": "allow_once"},
                )
            )
            respond_task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )
        elif t == "run.finished":
            finished.set()

    client.on_event(on_event)

    # ── subscribe ─────────────────────────────────────────────────────
    sub_result = await client.send_command("event.subscribe", {
        "topics": ["session.*", "run.*", "step.*", "tool.*",
                   "llm.token", "llm.usage", "log.*", "permission.*"],
        "scope": "global",
    })
    print(f"[subscribed] subscription_id={sub_result.get('subscription_id')}")

    # ── session.create ────────────────────────────────────────────────
    sess_result = await client.send_command("session.create", {"mode": "chat"})
    session_id = sess_result["session_id"]
    print(f"[session]   session_id={session_id}")

    # ── session.send_message ──────────────────────────────────────────
    print(f"\n[send]      goal={GOAL!r}\n")
    await client.send_command(
        "session.send_message", {"session_id": session_id, "content": GOAL}
    )

    # ── 等最多 60 秒（连接断开时提前返回） ─────────────────────────────
    wait_task = asyncio.create_task(finished.wait())
    done, _ = await asyncio.wait(
        {wait_task, client.loop_task},
        timeout=60,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if wait_task not in done:
        if client.loop_task in done:
            print("\n[disconnected] daemon 连接已断开")
        else:
            print("\n[TIMEOUT] 60 秒内未收到 run.finished")
        wait_task.cancel()

    await client.close()

    print("\n── 汇总 ──")
    types = [e.get("type") for e in events_log]
    for t in dict.fromkeys(types):   # preserve order, deduplicate
        cnt = types.count(t)
        print(f"  {t:40s} × {cnt}")

    if permission_event is None:
        print("\n[WARN] 未收到 permission.requested 事件！")
    else:
        print("\n[OK] 完整流程走通，permission.requested 已收到并回复。")


def _brief(e: dict[str, Any]) -> str:
    skip = {"type", "ts", "run_id", "session_id"}
    parts = [f"{k}={v!r}" for k, v in e.items() if k not in skip]
    s = "  ".join(parts)
    return s[:120] + "…" if len(s) > 120 else s


if __name__ == "__main__":
    asyncio.run(main())
