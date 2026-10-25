from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from desk_agent.core.config import DeskConfig

_TEXT_FMT = 'level=%(levelname)s ts=%(asctime)s source=%(name)s msg="%(message)s"'
_JSON_FMT = '{"level":"%(levelname)s","ts":"%(asctime)s","source":"%(name)s","msg":"%(message)s"}'


# 打开滚动文件 handler：日志文件不可用（被其它进程占用、目录只读、权限/沙箱限制）时返回 None，
# 由调用方决定如何降级——日志写不出来不应阻止进程启动
def open_rotating_file_handler(
    log_file: str | Path,
    *,
    max_bytes: int,
    backup_count: int,
) -> tuple[logging.handlers.RotatingFileHandler | None, OSError | None]:
    log_path = Path(log_file).expanduser()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    except OSError as exc:
        return None, exc
    return handler, None


# 根据配置初始化 root logger：设置级别、格式，并挂载 stderr 和可选的滚动文件 handler
def setup_logging(config: DeskConfig) -> None:
    level = getattr(logging, config.logging.level.upper(), logging.INFO)
    fmt = _JSON_FMT if config.logging.format == "json" else _TEXT_FMT
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    if config.logging.file:
        file_handler, error = open_rotating_file_handler(
            config.logging.file, max_bytes=10 * 1024 * 1024, backup_count=5
        )
        if file_handler is None:
            root.warning(
                "file logging disabled: cannot open %s (%s); logging to stderr only",
                Path(config.logging.file).expanduser(),
                error,
            )
        else:
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
