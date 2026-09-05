from __future__ import annotations

from pathlib import Path


# 工具路径必须相对当前工作目录（工具文档契约）。
# 只拦截 ".." 不拦截绝对路径会构成路径遍历漏洞，因此此处统一校验：
# - Windows 盘符绝对路径（is_absolute）
# - Windows 上的 "/x" / "\x"（drive-relative，root 非空，会指向当前盘根）
# - "~" 开头（expanduser 后变成绝对路径，逃逸 cwd）
# - ".." 父目录回溯
# 校验通过返回原样 Path，供调用方继续使用。
def require_relative_path(path_str: str) -> Path:
    path = Path(path_str)
    expanded = path.expanduser()
    if path.is_absolute() or bool(path.root) or expanded.is_absolute():
        raise PermissionError(
            f"absolute path not allowed (must be relative to the "
            f"current working directory): {path_str}"
        )
    if ".." in path.parts:
        raise PermissionError(f"path traversal not allowed: {path_str}")
    return path
