from __future__ import annotations

import sys
from typing import Any

from desk_agent.core.tools.builtin.query import software


class _FakeWinreg:
    """最小 winreg 替身：EnumKey / OpenKey / QueryValueEx / CloseKey + hive 常量。

    每个子键表示为 {value_name: value}；模拟缺失键 / 缺失值抛 OSError。
    """

    HKEY_LOCAL_MACHINE = object()
    HKEY_CURRENT_USER = object()

    def __init__(self, subkeys: dict[str, dict[str, Any]]) -> None:
        self._subkeys = subkeys

    def EnumKey(self, key: Any, index: int) -> str:  # noqa: N802 - 对齐 winreg API
        names = list(self._subkeys)
        if index >= len(names):
            raise OSError("no more subkeys")
        return names[index]

    def OpenKey(self, parent: Any, name: str) -> tuple[str, dict[str, Any]]:  # noqa: N802
        # 根键：query() 用注册表路径打开 Uninstall 根；子键：按名称打开
        if name in (software._ADD_REMOVE_PATH, software._WOW64_PATH):
            return ("root", name)
        if name not in self._subkeys:
            raise OSError("subkey not found")
        return (name, self._subkeys[name])

    def QueryValueEx(self, key: Any, value_name: str) -> tuple[Any, int]:  # noqa: N802
        _name, values = key
        if value_name not in values:
            raise OSError("value not found")
        return values[value_name], 1

    def CloseKey(self, key: Any) -> None:  # noqa: N802
        return None


def test_entries_skip_missing_displayname_and_system_components() -> None:
    fake = _FakeWinreg(
        {
            "chrome": {"DisplayName": "Chrome", "DisplayVersion": "126", "Publisher": "Google"},
            "no-name": {"DisplayVersion": "1.0"},  # 无 DisplayName → 跳过
            "component": {"DisplayName": "Win Component", "SystemComponent": 1},  # 默认跳过
        }
    )
    entries = software._entries_from_key(fake, object(), include_system_components=False)
    assert [e.name for e in entries] == ["Chrome"]


def test_entries_include_system_components_when_requested() -> None:
    fake = _FakeWinreg(
        {
            "component": {"DisplayName": "Win Component", "SystemComponent": 1},
        }
    )
    entries = software._entries_from_key(fake, object(), include_system_components=True)
    assert [e.name for e in entries] == ["Win Component"]


def test_entry_reads_all_fields() -> None:
    fake = _FakeWinreg(
        {
            "app": {
                "DisplayName": "  MyApp  ",
                "DisplayVersion": "2.1",
                "Publisher": "Acme",
                "InstallDate": "20240115",
            },
        }
    )
    (entry,) = software._entries_from_key(fake, object(), include_system_components=False)
    assert entry.name == "MyApp"
    assert entry.version == "2.1"
    assert entry.publisher == "Acme"
    assert entry.install_date == "2024-01-15"


def test_parse_install_date_formats() -> None:
    assert software._parse_install_date("20240115") == "2024-01-15"
    assert software._parse_install_date("20240115093000") == "2024-01-15"
    # REG_DWORD：Unix 时间戳按 UTC 换算，跨时区结果一致
    assert software._parse_install_date(1700000000) == "2023-11-14"
    # 无法解析时原样返回；缺失返回空串
    assert software._parse_install_date("2024-13-99") == "2024-13-99"
    assert software._parse_install_date(None) == ""


def test_format_dedupes_and_filters() -> None:
    entries = [
        software.SoftwareEntry("Firefox", "1.0", "Mozilla", ""),
        software.SoftwareEntry("firefox", "1.0", "mozilla", ""),  # 三视图重复（仅大小写不同）
        software.SoftwareEntry("Chrome", "126", "Google", "2024-01-15"),
    ]
    out = software._format(entries, name_filter="fire", max_results=10)
    assert "Firefox" in out
    assert "Chrome" not in out
    assert out.count("  - ") == 1  # 去重后只剩一条


def test_format_truncates_and_reports_count() -> None:
    entries = [software.SoftwareEntry(f"App {i}", "1.0", "P", "") for i in range(5)]
    out = software._format(entries, name_filter="", max_results=2)
    assert "2 of 5" in out
    assert "truncated" in out


def test_format_no_match_and_none() -> None:
    entries = [software.SoftwareEntry("Chrome", "126", "Google", "")]
    assert "no match" in software._format(entries, name_filter="zzz", max_results=10)
    assert "none found" in software._format([], name_filter="", max_results=10)


def test_query_graceful_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(software, "_IS_WINDOWS", False)
    out = software.query()
    assert "only available on Windows" in out


def test_query_with_fake_winreg(monkeypatch) -> None:
    """注入假 winreg，验证 query() 的完整链路（三视图 → 去重 → 格式化）。"""
    fake = _FakeWinreg(
        {
            "chrome": {"DisplayName": "Chrome", "DisplayVersion": "126", "Publisher": "Google"},
            "edge": {"DisplayName": "Edge", "DisplayVersion": "1.0"},
            "component": {"DisplayName": "Win Component", "SystemComponent": 1},
        }
    )
    monkeypatch.setattr(software, "_IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    out = software.query(name_filter="", max_results=10)
    assert "Chrome" in out
    assert "Edge" in out
    assert "Win Component" not in out  # 默认排除系统组件
    out = software.query(name_filter="chrome", max_results=10)
    assert "Chrome" in out
    assert "Edge" not in out
