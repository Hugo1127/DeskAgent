"""policy.toml [always] 存取单元测试：标准 tomllib 解析 + 脏文件容错。"""
from __future__ import annotations

import logging
from pathlib import Path

from desk_agent.core.permissions.storage import load_policy_file, save_policy_file


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_policy_file(tmp_path / "nope.toml") == {}


def test_load_always_table_parses_multiline_and_comments(tmp_path: Path) -> None:
    """多行字符串、注释内 `=` 不应干扰 [always] 解析（手写逐行解析的盲区）。"""
    f = tmp_path / "policy.toml"
    f.write_text(
        "# comment with = sign\n"
        'summary = """\n'
        "multi = line\n"
        '"""\n'
        "[always]\n"
        'bash = "allow"\n'
        'read_file = "deny"\n'
        "[other]\n"
        "x = 1\n",
        encoding="utf-8",
    )
    assert load_policy_file(f) == {"bash": "allow", "read_file": "deny"}


def test_load_skips_non_allow_deny_entries(tmp_path: Path, caplog) -> None:
    f = tmp_path / "policy.toml"
    f.write_text(
        "[always]\n"
        'write_file = "maybe"\n'  # 非 allow/deny 字符串，忽略
        "answer = true\n"          # 非字符串，忽略
        'bash = "allow"\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        result = load_policy_file(f)
    assert result == {"bash": "allow"}
    assert "invalid [always] entry" in caplog.text


def test_load_invalid_toml_returns_empty_with_warning(tmp_path: Path, caplog) -> None:
    f = tmp_path / "policy.toml"
    f.write_text("[always\nbash = \"allow\"\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_policy_file(f)
    assert result == {}
    assert "failed to parse" in caplog.text


def test_load_non_table_always_warns(tmp_path: Path, caplog) -> None:
    f = tmp_path / "policy.toml"
    f.write_text("always = \"nope\"\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_policy_file(f)
    assert result == {}
    assert "[always] must be a table" in caplog.text


def test_save_and_reload_roundtrip(tmp_path: Path) -> None:
    f = tmp_path / "policy.toml"
    save_policy_file({"bash": "allow", "read_file": "deny"}, f)
    assert load_policy_file(f) == {"bash": "allow", "read_file": "deny"}
