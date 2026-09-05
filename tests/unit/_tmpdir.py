"""测试共享的临时目录工具。

注：沙箱环境会锁定 tempfile / pytest 创建的目录（无法删除），
因此测试自建工作区临时目录（pathlib + uuid），结束后 shutil.rmtree 清理。
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path


class _TmpDir:
    """生成一个工作区内、可随 `with` 自动清理的临时目录。"""

    def __init__(self) -> None:
        self.path = Path(f".tmp-test-{uuid.uuid4().hex[:12]}")
        self.path.mkdir()

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
