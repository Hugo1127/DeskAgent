"""路径安全单元测试：read_file / read_image / list_dir 拒绝绝对路径与路径遍历。

工具文档约定 path 必须相对当前工作目录；早期实现只拦截 ".."，
不拦截绝对路径（含 Windows "/x" 盘根相对路径）会构成路径遍历漏洞。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from desk_agent.core.tools.builtin.list_dir import ListDirTool
from desk_agent.core.tools.builtin.read_file import ReadFileTool
from desk_agent.core.tools.builtin.read_image import ReadImageTool


def _invoke(tool, params: dict[str, object]):
    return asyncio.run(tool.invoke(params))


def test_read_file_rejects_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="absolute path not allowed"):
        _invoke(ReadFileTool(), {"path": str(Path("/etc/passwd"))})


def test_read_file_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="path traversal not allowed"):
        _invoke(ReadFileTool(), {"path": "../secret.txt"})


def test_read_image_rejects_absolute_path() -> None:
    result = _invoke(ReadImageTool(), {"path": str(Path("/etc/secret.jpg"))})
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "absolute path not allowed" in result.content
    assert result.error_type == "permission_denied"


def test_read_image_rejects_parent_traversal() -> None:
    result = _invoke(ReadImageTool(), {"path": "../secret.jpg"})
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "path traversal not allowed" in result.content


def test_read_image_rejects_tilde_home() -> None:
    # ~ 展开后是绝对路径，同样逃逸当前工作目录，应被拒绝
    result = _invoke(ReadImageTool(), {"path": "~/secret.jpg"})
    assert result.is_error is True
    assert isinstance(result.content, str)
    assert "absolute path not allowed" in result.content


def test_list_dir_rejects_absolute_path() -> None:
    with pytest.raises(PermissionError, match="absolute path not allowed"):
        _invoke(ListDirTool(), {"path": str(Path("/etc"))})


def test_list_dir_rejects_parent_traversal() -> None:
    with pytest.raises(PermissionError, match="path traversal not allowed"):
        _invoke(ListDirTool(), {"path": ".."})


def test_relative_paths_still_work(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path)

    result = _invoke(ReadFileTool(), {"path": "a.txt"})
    assert result.is_error is False
    assert result.content == "hello"

    result = _invoke(ListDirTool(), {"path": "."})
    assert result.is_error is False
    assert "a.txt" in result.content
