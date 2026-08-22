"""已安装软件查询实现：system_query 工具的 software 类别。

实现原理：直接读取 Windows 注册表 Uninstall 键，而不是 WMI 的
Win32_Product（后者只能看到 Windows Installer 安装的程序）。注册表方法：
- 覆盖几乎所有安装方式（MSI / NSIS / Inno 等）；
- 无需管理员权限，可同时读取 64 位 / 32 位 / 当前用户三个视图；
- 直接读注册表，比 WMI 查询更快。

数据来源（三个视图，遍历后按 (名称, 版本, 发布者) 去重）：
- HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall        （64 位程序）
- HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall （32 位程序）
- HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall        （当前用户程序）

读取字段：DisplayName / DisplayVersion / Publisher / InstallDate。
默认跳过 SystemComponent=1（系统组件）与无 DisplayName 的条目。

本模块只提供实现（query + 辅助函数），不定义 BaseTool——
统一入口在 query/tool.py 的 SystemQueryTool。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_IS_WINDOWS = sys.platform == "win32"

_ADD_REMOVE_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_WOW64_PATH = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"

# 默认/上限输出条数（工具层还会再用 Field 约束一次）
_MAX_RESULTS = 200
_MAX_RESULTS_LIMIT = 1000


@dataclass(frozen=True)
class SoftwareEntry:
    name: str
    version: str
    publisher: str
    install_date: str


def _to_int(value: Any) -> int:
    """注册表值可能是 int（REG_DWORD）或 str，统一转成 int。"""
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _parse_install_date(raw: Any) -> str:
    """把注册表里的 InstallDate 转成 YYYY-MM-DD 字符串。

    常见两种存储形式：
    - REG_SZ：'20240115' 或 '20240115093000'
    - REG_DWORD：Unix 时间戳（秒，按 UTC 换算，保证跨时区一致）
    无法解析时原样返回。
    """
    if raw is None:
        return ""
    if isinstance(raw, int):
        try:
            return datetime.fromtimestamp(raw, tz=UTC).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return str(raw)
    text = str(raw).strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def _entry_from_values(
    winreg_mod: Any, key: Any, include_system_components: bool
) -> SoftwareEntry | None:
    """从单个 Uninstall 子键读取条目；不满足展示条件（无名称 / 系统组件）返回 None。"""

    def get(name: str) -> Any:
        try:
            value, _ = winreg_mod.QueryValueEx(key, name)
            return value
        except OSError:
            return None

    display_name = get("DisplayName")
    if not display_name or not str(display_name).strip():
        return None
    if not include_system_components and _to_int(get("SystemComponent")) == 1:
        return None
    return SoftwareEntry(
        name=str(display_name).strip(),
        version=str(get("DisplayVersion") or "").strip(),
        publisher=str(get("Publisher") or "").strip(),
        install_date=_parse_install_date(get("InstallDate")),
    )


def _entries_from_key(
    winreg_mod: Any, key: Any, include_system_components: bool
) -> list[SoftwareEntry]:
    """遍历一个 Uninstall 根键下的所有子键（每个子键对应一个软件）。"""
    entries: list[SoftwareEntry] = []
    index = 0
    while True:
        try:
            subkey_name = winreg_mod.EnumKey(key, index)
        except OSError:
            break
        index += 1
        try:
            subkey = winreg_mod.OpenKey(key, subkey_name)
        except OSError:
            continue
        try:
            entry = _entry_from_values(winreg_mod, subkey, include_system_components)
        except OSError:
            entry = None
        finally:
            winreg_mod.CloseKey(subkey)
        if entry is not None:
            entries.append(entry)
    return entries


def _format(entries: list[SoftwareEntry], name_filter: str, max_results: int) -> str:
    """去重 / 过滤 / 排序 / 截断，输出人类可读的多行文本。"""
    max_results = max(1, min(int(max_results), _MAX_RESULTS_LIMIT))

    # 去重：同一软件可能同时出现在 64 位 / 32 位 / 用户三个视图中
    seen: set[tuple[str, str, str]] = set()
    unique: list[SoftwareEntry] = []
    for entry in entries:
        dedup_key = (entry.name.casefold(), entry.version, entry.publisher.casefold())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        unique.append(entry)

    if name_filter:
        needle = name_filter.casefold()
        unique = [e for e in unique if needle in e.name.casefold()]

    unique.sort(key=lambda e: (e.name.casefold(), e.version))

    total = len(unique)
    if not total:
        if name_filter:
            return f"installed software: no match for name_filter={name_filter!r}"
        return "installed software: none found"

    shown = unique[:max_results]
    lines = [f"installed software: {len(shown)} of {total} shown"]
    for entry in shown:
        parts = [entry.name]
        if entry.version:
            parts.append(f"v{entry.version}")
        meta: list[str] = []
        if entry.publisher:
            meta.append(entry.publisher)
        if entry.install_date:
            meta.append(f"installed {entry.install_date}")
        lines.append("  - " + " ".join(parts) + (f" ({', '.join(meta)})" if meta else ""))
    if total > max_results:
        lines.append(
            f"(truncated: {total - max_results} more — "
            "narrow with name_filter or raise max_results)"
        )
    return "\n".join(lines)


# 查询已安装软件并格式化为文本；只读、绝不抛出（非 Windows 返回提示文本）
def query(
    name_filter: str = "",
    include_system_components: bool = False,
    max_results: int = _MAX_RESULTS,
) -> str:
    if not _IS_WINDOWS:
        return (
            "installed software query reads the Windows registry and is only "
            f"available on Windows (current platform: {sys.platform})"
        )
    import winreg  # 仅 Windows 可用（sys.platform == 'win32' 已在上方判断）

    raw: list[SoftwareEntry] = []
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, _ADD_REMOVE_PATH),
        (winreg.HKEY_LOCAL_MACHINE, _WOW64_PATH),
        (winreg.HKEY_CURRENT_USER, _ADD_REMOVE_PATH),
    ):
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        try:
            raw.extend(_entries_from_key(winreg, key, include_system_components))
        finally:
            winreg.CloseKey(key)

    return _format(raw, name_filter, max_results)
