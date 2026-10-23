#!/usr/bin/env python3
"""统计仓库代码量：按语言/目录汇总文件数、总行、代码行、注释行、空行。

只统计 git 跟踪的文件（.venv / .git / 缓存目录自动被排除）。
用法:
    python scripts/count_lines.py            # 汇总表
    python scripts/count_lines.py --by-file  # 附每个文件明细
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tokenize
from collections import defaultdict
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 扩展名 -> 语言名；未列出的按“数据/其他”处理（只算总行）
LANG_BY_SUFFIX = {
    ".py": "Python",
    ".js": "JavaScript",
    ".css": "CSS",
    ".html": "HTML",
    ".md": "Markdown",
    ".toml": "TOML",
    ".typed": "Type-stub 标记",
}


@dataclass
class Stats:
    """单个文件或分组的计数容器。"""

    files: int = 0
    total: int = 0
    code: int = 0
    comment: int = 0
    blank: int = 0

    def add(self, other: Stats) -> None:
        self.files += other.files
        self.total += other.total
        self.code += other.code
        self.comment += other.comment
        self.blank += other.blank

    @property
    def net(self) -> int:
        """净代码行 = 代码 + 注释（不含空行）。"""
        return self.code + self.comment


def tracked_files() -> list[Path]:
    """返回 git 跟踪的文件绝对路径列表。"""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [REPO / line for line in out if line.strip()]


def count_python(text: str) -> tuple[int, int, int]:
    """用 tokenize 精确统计 Python 的代码/注释/空行（含 docstring 计入代码）。

    返回 (code, comment, blank)。
    """
    lines = text.splitlines()
    blank = sum(1 for line in lines if not line.strip())
    comment_lines: set[int] = set()

    try:
        for tok in tokenize.generate_tokens(StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                comment_lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # 语法不完整时退化为逐行前缀判断
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith("#"):
                comment_lines.add(i)

    comment = len(comment_lines)
    # 代码行 = 非空行中不是纯注释行的那部分
    code = 0
    for i, line in enumerate(lines, 1):
        if line.strip() and i not in comment_lines:
            code += 1
    return code, comment, blank


def count_c_like(text: str, line_marker: str, block: tuple[str, str]) -> tuple[int, int, int]:
    """统计类 C 语法（JS/CSS）的代码与注释行。"""
    open_tok, close_tok = block
    code = comment = blank = 0
    in_block = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            blank += 1
            continue
        if in_block:
            comment += 1
            if close_tok in line:
                in_block = False
            continue
        if line.startswith(line_marker):
            comment += 1
            continue
        if line.startswith(open_tok):
            comment += 1
            if close_tok not in line:
                in_block = True
            continue
        code += 1

    return code, comment, blank


def count_markup(text: str) -> tuple[int, int, int]:
    """统计 Markdown/HTML 的正文与注释行。"""
    code = comment = blank = 0
    in_html_comment = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            blank += 1
            continue
        if in_html_comment:
            comment += 1
            if "-->" in line:
                in_html_comment = False
            continue
        if line.startswith("<!--"):
            comment += 1
            if "-->" not in line:
                in_html_comment = True
            continue
        code += 1

    return code, comment, blank


def count_config(text: str) -> tuple[int, int, int]:
    """统计 TOML/其他配置：以 # 开头的行算注释。"""
    code = comment = blank = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            blank += 1
        elif line.startswith("#"):
            comment += 1
        else:
            code += 1
    return code, comment, blank


def count_file(path: Path) -> Stats:
    """统计单个文件的各类行数。无法解码的按空计。"""
    stats = Stats(files=1)
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return stats

    suffix = path.suffix.lower()
    if suffix == ".py":
        code, comment, blank = count_python(text)
    elif suffix in {".js", ".css"}:
        code, comment, blank = count_c_like(text, "//", ("/*", "*/"))
    elif suffix in {".md", ".html"}:
        code, comment, blank = count_markup(text)
    else:
        code, comment, blank = count_config(text)

    stats.code, stats.comment, stats.blank = code, comment, blank
    stats.total = len(text.splitlines())
    return stats


def top_group(rel: str) -> str:
    """把文件路径归到顶层目录（根目录文件归为 <root>）。"""
    parts = rel.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else "<root>"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--by-file", action="store_true", help="输出每个文件的明细")
    args = parser.parse_args()

    by_lang: dict[str, Stats] = defaultdict(Stats)
    by_group: dict[str, Stats] = defaultdict(Stats)
    per_file: list[tuple[str, str, Stats]] = []
    grand = Stats()

    for path in tracked_files():
        rel = path.relative_to(REPO).as_posix()
        stats = count_file(path)
        lang = LANG_BY_SUFFIX.get(path.suffix.lower(), "其他")
        by_lang[lang].add(stats)
        by_group[top_group(rel)].add(stats)
        grand.add(stats)
        per_file.append((rel, lang, stats))

    def row(label: str, s: Stats) -> str:
        cells = (
            f"{label:<18}",
            f"{s.files:>7}",
            f"{s.total:>9}",
            f"{s.code:>9}",
            f"{s.comment:>9}",
            f"{s.blank:>9}",
            f"{s.net:>9}",
        )
        return "".join(cells)

    header = f"{'语言/目录':<18}{'文件':>7}{'总行':>9}{'代码':>9}{'注释':>9}{'空行':>9}{'净行':>9}"
    sep = "-" * len(header)

    print("仓库代码量统计（仅 git 跟踪文件，已排除 .venv/.git/缓存）")
    print()
    print(header)
    print(sep)
    for lang, s in sorted(by_lang.items(), key=lambda kv: kv[1].total, reverse=True):
        print(row(lang, s))
    print(sep)
    print(row("合计", grand))
    print()
    print("按顶层目录")
    print(header)
    print(sep)
    for group, s in sorted(by_group.items(), key=lambda kv: kv[1].total, reverse=True):
        print(row(group, s))
    print(sep)
    print(row("合计", grand))

    if args.by_file:
        print()
        print("按文件（降序）")
        print(f"{'行':>7}  {'代码':>7}  {'注释':>7}  文件")
        for rel, _lang, s in sorted(per_file, key=lambda x: x[2].total, reverse=True):
            print(f"{s.total:>7}  {s.code:>7}  {s.comment:>7}  {rel}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
