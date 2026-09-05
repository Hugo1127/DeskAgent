from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

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

# 测试图片用 base64 内联生成，不依赖仓库中的磁盘图片文件（CI 可稳定运行）
_JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q=="
)
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _invoke(tool, params: dict[str, object]):
    return asyncio.run(tool.invoke(params))


# 夹具：隔离目录内生成 1.jpg（内联 JPEG 字节），并把 cwd 切到该目录
@pytest.fixture
def image_dir(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "1.jpg").write_bytes(_JPEG_BYTES)
    (tmp_path / "1.png").write_bytes(_PNG_BYTES)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------- read_image 工具 ----------


def test_read_image_returns_image_block(image_dir: Path) -> None:
    result = _invoke(ReadImageTool(), {"path": "1.jpg"})

    assert result.is_error is False
    assert isinstance(result.content, list)
    assert len(result.content) == 2
    text_block, image_block = result.content
    assert text_block["type"] == "text"
    assert "1.jpg" in text_block["text"]
    assert image_block["type"] == "image"
    source = image_block["source"]
    assert source["type"] == "base64"
    assert source["media_type"] == "image/jpeg"
    assert base64.b64decode(source["data"]) == _JPEG_BYTES


def test_read_image_missing_file(image_dir: Path) -> None:
    result = _invoke(ReadImageTool(), {"path": "nope.png"})
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "不存在" in result.content


def test_read_image_unsupported_format(image_dir: Path) -> None:
    # 存在的非图片文件（.toml）
    (image_dir / "notes.toml").write_text("x = 1", encoding="utf-8")
    result = _invoke(ReadImageTool(), {"path": "notes.toml"})
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "不支持的图片格式" in result.content


def test_read_image_oversize(image_dir: Path, monkeypatch) -> None:
    # 不写大文件：把大小上限临时调小，用内联小图片触发超限
    (image_dir / "big.jpg").write_bytes(b"x" * 100)
    monkeypatch.setattr(image_blocks_mod, "MAX_IMAGE_BYTES", 10)
    result = _invoke(ReadImageTool(), {"path": "big.jpg"})
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "大小上限" in result.content


def test_read_image_ignores_extra_params(image_dir: Path) -> None:
    # extra="ignore"：多余参数不报 schema 错，仍走路径校验
    result = _invoke(ReadImageTool(), {"path": "nope.png", "extra": 1})
    assert result.is_error is True
    assert "不存在" in result.content


# ---------- read_file 定位：图片提示 ----------


def test_read_file_hints_read_image_for_images(image_dir: Path) -> None:
    result = _invoke(ReadFileTool(), {"path": "1.jpg"})
    assert result.is_error is True
    assert "read_image" in result.content


def test_read_file_still_reads_text(image_dir: Path) -> None:
    (image_dir / "app.toml").write_text(
        'name = "desk-agent"\nrequires = ["hatchling"]\n', encoding="utf-8"
    )
    result = _invoke(ReadFileTool(), {"path": "app.toml"})
    assert result.is_error is False
    assert isinstance(result.content, str)
    assert "hatchling" in result.content


# ---------- attachments 辅助 ----------


def test_image_content_block_roundtrip(image_dir: Path) -> None:
    block = image_content_block("1.jpg")
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(block["source"]["data"]) == _JPEG_BYTES


def test_image_block_placeholder_no_base64_leak() -> None:
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


def test_truncate_tool_results_keeps_list_content() -> None:
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


def test_messages_to_text_redacts_image_blocks() -> None:
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


def test_estimate_tokens_counts_image_as_fixed() -> None:
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


def test_estimate_image_tokens_extension_point() -> None:
    # 未来按尺寸精确估算的扩展点：当前返回固定占位值
    block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    assert estimate_image_tokens(block) == IMAGE_TOKEN_ESTIMATE
