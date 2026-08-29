"""desk-web 入口：解析参数 → 构建配置 → 启动桥接与 FastAPI 服务。

前台运行（同 `desk-core` 前台模式），Ctrl+C 停止；日志写文件（不干扰浏览器）。
daemon 未就绪时不会退出：先起 Web 服务，桥接层持续指数退避重连。
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn

from desk_agent.core.config import get_config
from desk_agent.web.bridge import CoreBridge
from desk_agent.web.server import WebServerConfig, create_app


# desk-web 文件日志初始化：不写 stderr（保持前台输出干净），只写滚动文件
def _setup_logging(level: str, log_file: str) -> None:
    log_path = Path(log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            'level=%(levelname)s ts=%(asctime)s source=%(name)s msg="%(message)s"',
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    root.handlers.clear()
    root.addHandler(handler)


# 计算可访问的 Web UI URL；监听 0.0.0.0 / :: 时回退为回环地址（浏览器无法访问 0.0.0.0）
def _display_url(host: str, port: int) -> str:
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{port}/"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="desk-web", description="DeskAgent 本地 Web 界面"
    )
    parser.add_argument(
        "--host", default=None, help="Web 服务监听地址（覆盖配置 web.host，兜底 127.0.0.1）"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Web 服务端口（覆盖配置 web.port，兜底 9987）"
    )
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--log-level", default=None, help="日志级别（DEBUG/INFO/WARNING/ERROR）")
    args = parser.parse_args()

    config = get_config()

    # 命令行参数 > 分层配置（TOML / .env / 环境变量）> 内建默认值
    host = args.host if args.host is not None else config.web.host
    port = args.port if args.port is not None else config.web.port
    token = config.web.token
    log_level = args.log_level or os.environ.get("DESK_LOG_LEVEL", config.logging.level)

    _setup_logging(log_level, config.web.log_file)

    web_config = WebServerConfig(host=host, port=port, token=token)
    bridge = CoreBridge(config)
    app = create_app(config, web_config, bridge)

    # 无论是否 --open，都在终端打印 URL，方便关掉浏览器后随时重新打开
    url = _display_url(host, port)
    print(f"DeskAgent Web UI: {url}", flush=True)

    if args.open:
        # --open 仅同机有效：自动打开默认浏览器
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # log_config=None 复用上方文件日志（避免 uvicorn 重新配置 logging 打到 stderr）
    uvicorn.run(app, host=host, port=port, log_level=log_level.lower(), log_config=None)


if __name__ == "__main__":
    main()
