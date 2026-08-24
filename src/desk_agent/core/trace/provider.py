from __future__ import annotations

import dataclasses
import time
from datetime import UTC, datetime
from typing import Any

from desk_agent.core.events.bus import EventBus
from desk_agent.core.llm.base import LLMProvider
from desk_agent.core.llm.image_blocks import image_block_placeholder
from desk_agent.core.llm.types import LlmResponse
from desk_agent.core.trace.record import TraceRecord
from desk_agent.core.trace.writer import TraceWriter


def _now() -> str:
    return datetime.now(UTC).isoformat()


# 递归脱敏 content blocks：任意层级（顶层 user content 或 tool_result 内）的 image 块
# 替换为占位符，防止 base64 数据撑爆 trace 文件
def _redact_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in blocks:
        btype = block.get("type")
        if btype == "image":
            out.append({"type": "image", "placeholder": image_block_placeholder(block)})
        elif btype == "tool_result" and isinstance(block.get("content"), list):
            redacted = dict(block)
            redacted["content"] = _redact_blocks(block["content"])
            out.append(redacted)
        else:
            out.append(block)
    return out


def _redact_messages(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            m = dict(msg)
            m["content"] = _redact_blocks(content)
            out.append(m)
        else:
            out.append(msg)
    return out


class TracingProvider:
    # 包裹真实 LLMProvider，在每次 chat() 调用前后向 TraceWriter 写入完整 API I/O 记录
    def __init__(
        self,
        inner: LLMProvider,
        trace: TraceWriter,
        *,
        include_payload: bool = True,
    ) -> None:
        self._inner = inner
        self._trace = trace
        self._include_payload = include_payload

    # 记录 CORE→LLM 请求，调用真实 provider，记录 LLM→CORE 响应（含延迟）
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
        *,
        step: int = 0,
        system: str | None = None,
    ) -> LlmResponse:
        call_data: dict[str, Any]
        if self._include_payload:
            call_data = {
                "messages": _redact_messages(messages),
                "tool_schemas": tool_schemas,
                "system": system,
            }
        else:
            call_data = {
                "message_count": len(messages),
                "tool_count": len(tool_schemas),
            }

        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="CORE→LLM",
                layer="llm",
                kind="api_call",
                run_id=run_id,
                step=step,
                data=call_data,
            )
        )

        t0 = time.monotonic()
        result = await self._inner.chat(
            messages, tool_schemas, bus, run_id, step=step, system=system
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        resp_data: dict[str, Any]
        if self._include_payload:
            resp_data = {
                "stop_reason": result.stop_reason,
                "text": result.text,
                "tool_calls": [dataclasses.asdict(tc) for tc in result.tool_calls],
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }
        else:
            resp_data = {
                "stop_reason": result.stop_reason,
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }

        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="LLM→CORE",
                layer="llm",
                kind="api_response",
                run_id=run_id,
                step=step,
                data=resp_data,
            )
        )

        return result
