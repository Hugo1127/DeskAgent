from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import desk_agent.core.llm.image_blocks as image_blocks_mod
from desk_agent.core.compact.budget import truncate_tool_results
from desk_agent.core.compact.compactor import _estimate_tokens, _messages_to_text
from desk_agent.core.llm.image_blocks import (
    IMAGE_TOKEN_ESTIMATE,
    estimate_image_tokens,
    image_block_placeholder,
    image_content_block,
)
from desk_agent.core.tools.builtin.read_file import ReadFileTool
from desk_agent.core.tools.builtin.read_image import ReadImageTool

# 测试夹具：项目内真实 JPEG 图片（只读，无临时文件写入）
_IMG = Path(__file__).resolve().parent.parent.parent / "workspace" / "1.jpg"
_TXT = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"


def _invoke(tool, params: dict[str, object]):
    return asyncio.run(tool.invoke(params))


# ---------- read_image 工具 ----------


def test_read_image_returns_image_block():
    assert _IMG.is_file()

    result = _invoke(ReadImageTool(), {"path": str(_IMG)})

    assert result.is_error is False
    assert isinstance(result.content, list)
    assert len(result.content) == 2
    text_block, image_block = result.content
    assert text_block["type"] == "text"
    assert str(_IMG) in text_block["text"]
    assert image_block["type"] == "image"
    source = image_block["source"]
    assert source["type"] == "base64"
    assert source["media_type"] == "image/jpeg"
    assert base64.b64decode(source["data"]) == _IMG.read_bytes()


def test_read_image_missing_file():
    result = _invoke(ReadImageTool(), {"path": str(_IMG.parent / "nope.png")})
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "不存在" in result.content


def test_read_image_unsupported_format():
    # 存在的非图片文件（.toml）
    result = _invoke(ReadImageTool(), {"path": str(_TXT)})
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "不支持的图片格式" in result.content


def test_read_image_oversize(monkeypatch):
    # 不写大文件：把大小上限临时调小，用真实图片触发超限
    monkeypatch.setattr(image_blocks_mod, "MAX_IMAGE_BYTES", 10)
    result = _invoke(ReadImageTool(), {"path": str(_IMG)})
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "大小上限" in result.content


def test_read_image_ignores_extra_params():
    # extra="ignore"：多余参数不报 schema 错，仍走路径校验
    result = _invoke(ReadImageTool(), {"path": "definitely-missing.png", "extra": 1})
    assert result.is_error is True
    assert "不存在" in result.content


# ---------- read_file 定位：图片提示 ----------


def test_read_file_hints_read_image_for_images():
    result = _invoke(ReadFileTool(), {"path": str(_IMG)})
    assert result.is_error is True
    assert "read_image" in result.content


def test_read_file_still_reads_text():
    result = _invoke(ReadFileTool(), {"path": str(_TXT)})
    assert result.is_error is False
    assert isinstance(result.content, str)
    assert "hatchling" in result.content


# ---------- attachments 辅助 ----------


def test_image_content_block_roundtrip():
    block = image_content_block(str(_IMG))
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(block["source"]["data"]) == _IMG.read_bytes()


def test_image_block_placeholder_no_base64_leak():
    block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(b"x" * 2048).decode(),
        },
    }
    placeholder = image_block_placeholder(block)
    assert "[image: image/png" in placeholder
    assert "base64" not in placeholder


# ---------- 下游链路安全 ----------


def test_truncate_tool_results_keeps_list_content():
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": [
                        {"type": "text", "text": "hello" * 100},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                    ],
                }
            ],
        }
    ]
    result = truncate_tool_results(messages)
    tool_result = result[0]["content"][0]
    assert isinstance(tool_result["content"], list)
    assert tool_result["content"][1]["type"] == "image"


def test_messages_to_text_redacts_image_blocks():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "see this"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJDREU="}},
            ],
        }
    ]
    text = _messages_to_text(messages)
    assert "[image:" in text
    assert "QUJDREU=" not in text  # base64 不泄露


def test_estimate_tokens_counts_image_as_fixed():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 1_000_000}},
            ],
        }
    ]
    estimate = _estimate_tokens(messages)
    assert estimate <= IMAGE_TOKEN_ESTIMATE + 16  # 1M 字符 base64 不再算成天量
    assert estimate >= IMAGE_TOKEN_ESTIMATE


def test_estimate_image_tokens_extension_point():
    # 未来按尺寸精确估算的扩展点：当前返回固定占位值
    block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    assert estimate_image_tokens(block) == IMAGE_TOKEN_ESTIMATE
