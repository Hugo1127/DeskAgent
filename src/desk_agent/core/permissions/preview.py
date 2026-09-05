from __future__ import annotations

import asyncio
import difflib
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from desk_agent.core.tools.path_guard import require_relative_path

_MAX_PREVIEW_CHARS = 4000
_MAX_DIFF_LINES = 60
_MAX_DIFF_CHARS = 2500
_SYNTAX_TIMEOUT_S = 5.0

# 静态危险特征标注：仅提示，不构成策略判断
_DANGEROUS_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-rf\b"), "递归强制删除（rm -rf）"),
    (re.compile(r"\brm\s+-r\b"), "递归删除（rm -r）"),
    (re.compile(r"\b(?:del|erase)\b", re.IGNORECASE), "删除文件（del/erase）"),
    (re.compile(r"\b(?:format|mkfs|fdisk|dd)\b"), "磁盘格式化/分区/块级写入"),
    (re.compile(r"\bshutdown\b", re.IGNORECASE), "关机/重启"),
    (re.compile(r">\s*[^\s>|]+"), "重定向覆盖文件"),
    (re.compile(r"\b(?:curl|wget)\s+-[^ ]*o|iwr\b", re.IGNORECASE), "下载文件到本地"),
    (re.compile(r"\b(?:pip|npm|uv|apt|yum|choco|winget)\b"), "包管理器操作"),
]


def _clip(text: str, limit: int = _MAX_PREVIEW_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…[preview truncated]"

# Git for Windows 常见安装位置（daemon 的 PATH 未必包含 bash，预览时兜底探测）
_GIT_BASH_CANDIDATES: tuple[str, ...] = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)

# 相对 Git 安装根目录的 bash 候选路径（兼容不同安装布局）
_GIT_BASH_RELATIVES: tuple[str, ...] = (
    "bin/bash.exe",
    "usr/bin/bash.exe",
)
_GIT_EXEC_PATH_TIMEOUT_S = 3.0


def _bash_candidates_under(root: Path) -> list[str]:
    """返回 Git 安装根目录下的候选 bash 路径。"""
    return [str(root / relative) for relative in _GIT_BASH_RELATIVES]


def _git_root_from_git_exe(git: str) -> Path | None:
    """从 git 可执行文件位置向上推导 Git 安装根目录（无需启动子进程）。"""
    path = Path(git).resolve()
    for _ in range(5):
        if (path / "bin" / "bash.exe").is_file() or (
            path / "usr" / "bin" / "bash.exe"
        ).is_file():
            return path
        parent = path.parent
        if parent == path:
            break
        path = parent
    return None


def _git_root_from_exec_path(git: str) -> Path | None:
    """通过 `git --exec-path` 推导 Git 安装根目录（覆盖非默认安装位置）。"""
    try:
        result = subprocess.run(
            [git, "--exec-path"],
            capture_output=True,
            text=True,
            timeout=_GIT_EXEC_PATH_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    exec_path = result.stdout.strip()
    if not exec_path:
        return None
    path = Path(exec_path)
    for _ in range(6):
        if (path / "bin" / "bash.exe").is_file() or (
            path / "usr" / "bin" / "bash.exe"
        ).is_file():
            return path
        parent = path.parent
        if parent == path:
            break
        path = parent
    return None


# 探测可用的 bash：优先 DESK_BASH_PATH 环境变量，其次 PATH，然后常见 Git 安装位置，
# 最后从 git 可执行文件 / `git --exec-path` 推导非默认安装位置（如 D:\Programs\Git）
def _find_bash() -> str | None:
    env_path = os.environ.get("DESK_BASH_PATH", "").strip()
    if env_path and Path(env_path).is_file():
        return env_path
    found = shutil.which("bash")
    if found:
        return found
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    candidates = list(_GIT_BASH_CANDIDATES)
    if local_appdata:
        candidates.insert(
            0, str(Path(local_appdata) / "Programs" / "Git" / "bin" / "bash.exe")
        )
    git = shutil.which("git")
    if git:
        root = _git_root_from_git_exe(git)
        if root is not None:
            candidates.extend(_bash_candidates_under(root))
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    # 最后兜底：git.exe 路径推导失败时（如经 shim 调用），走 git --exec-path
    if git:
        root = _git_root_from_exec_path(git)
        if root is not None:
            for candidate in _bash_candidates_under(root):
                if Path(candidate).is_file():
                    return candidate
    return None


# bash 预览：仅展示命令 + 静态标注 + bash -n 语法检查（绝不执行命令本身）
async def _preview_bash(params: dict[str, Any]) -> str:
    command = str(params.get("command", ""))
    timeout = int(params.get("timeout", 60))
    lines = [
        "命令（不执行，仅静态预览）:",
        f"  {command}",
        f"  当前工作目录: {os.getcwd()}",
        f"  超时设置: {timeout}s",
    ]
    hits = [label for pat, label in _DANGEROUS_HINTS if pat.search(command)]
    if hits:
        lines.append("⚠ 危险特征提示: " + "；".join(hits))

    bash_path = await asyncio.to_thread(_find_bash)
    if bash_path is not None:
        try:
            proc = await asyncio.create_subprocess_exec(
                bash_path, "-n", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=_SYNTAX_TIMEOUT_S
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                lines.append("  ⚠ 语法检查超时（预览已中止，命令未执行）")
            else:
                if proc.returncode == 0:
                    lines.append("  ✓ 语法检查通过（bash -n，未执行）")
                else:
                    err = out.decode("utf-8", errors="replace").strip()
                    lines.append("  ✗ 语法错误（bash -n，未执行）:")
                    lines.append("    " + err.replace("\n", "\n    "))
        except OSError:
            lines.append("  ⚠ 无法启动 bash，跳过语法检查")
    else:
        lines.append(
            "  ⚠ 未找到 bash，跳过语法检查（可将 Git Bash 加入 PATH、"
            "设置 DESK_BASH_PATH，或确保 git 在 PATH 中以便自动推导安装位置）"
        )
    return _clip("\n".join(lines))


# write_file 预览：目标文件状态 + 新旧内容 diff（只读，不写入）
def _preview_write_file(params: dict[str, Any]) -> str:
    path_str = str(params.get("path", ""))
    content = str(params.get("content", ""))
    try:
        path = require_relative_path(path_str)
    except PermissionError as exc:
        return _clip(f"⚠ 路径校验被拒绝: {exc}")

    encoded = content.encode("utf-8")
    exists = path.exists()
    lines = [f"目标文件: {path.resolve()}", f"已存在: {exists}"]
    if exists and path.is_file():
        old = path.read_text(encoding="utf-8", errors="replace")
        lines.append(f"当前大小: {path.stat().st_size} 字节 → 新大小: {len(encoded)} 字节")
        diff = "\n".join(
            list(
                difflib.unified_diff(
                    old.splitlines(),
                    content.splitlines(),
                    fromfile=f"{path_str} (当前)",
                    tofile=f"{path_str} (写入后)",
                    lineterm="",
                )
            )[:_MAX_DIFF_LINES]
        )
        if diff:
            lines.append("变更 diff（预览，不写入）:")
            lines.append(_clip(diff, _MAX_DIFF_CHARS))
        else:
            lines.append("内容与当前文件一致，写入无变化")
    elif exists:
        lines.append("目标存在但不是普通文件，写入行为需谨慎")
    else:
        parent = path.parent
        lines.append(f"父目录: {parent}  存在: {parent.exists()}")
        lines.append(f"新增文件: {len(encoded)} 字节")
    return _clip("\n".join(lines))


# 文件类只读工具的元数据预览（read_file / read_image / list_dir）
def _preview_path_stat(tool_name: str, params: dict[str, Any]) -> str:
    path_str = str(params.get("path", "."))
    try:
        path = require_relative_path(path_str)
    except PermissionError as exc:
        return _clip(f"⚠ 路径校验被拒绝: {exc}")

    lines = [f"{tool_name} 参数: path={path_str!r}"]
    try:
        st = path.stat()
    except FileNotFoundError:
        lines.append("目标不存在（工具调用会返回错误）")
        return _clip("\n".join(lines))
    except OSError as exc:
        lines.append(f"无法读取元数据: {exc}")
        return _clip("\n".join(lines))

    kind = "目录" if path.is_dir() else ("文件" if path.is_file() else "其他")
    mtime = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    lines.append(f"解析路径: {path.resolve()}")
    lines.append(f"类型: {kind}  大小: {st.st_size} 字节  修改时间: {mtime}")
    return _clip("\n".join(lines))


# note_save 预览：展示将写入 notes 的内容片段
def _preview_note_save(params: dict[str, Any]) -> str:
    content = str(params.get("content", ""))
    snippet = content if len(content) <= 200 else content[:200] + "…"
    return _clip(f"将写入 session notes（{len(content)} 字符）:\n{snippet}")


# 对工具调用生成只读预览文本；预览不执行任何副作用操作
async def preview_tool(tool_name: str, params: dict[str, Any]) -> str:
    if tool_name == "bash":
        return await _preview_bash(params)
    if tool_name == "write_file":
        return _preview_write_file(params)
    if tool_name in ("read_file", "read_image", "list_dir"):
        return _preview_path_stat(tool_name, params)
    if tool_name == "note_save":
        return _preview_note_save(params)
    if not params:
        return "(无参数)"
    import json
    return _clip("参数: " + json.dumps(params, ensure_ascii=False))
