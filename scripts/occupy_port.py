#!/usr/bin/env python3
"""
简单端口占用脚本：绑定并监听一个 TCP 端口，直到 Ctrl+C 退出。

用于测试 DeskAgent 的 system_query 工具（category='port'，查看占用进程）
以及配合 bash 工具执行 kill 命令释放端口。

运行：
    uv run python scripts/occupy_port.py            # 默认占用 5173
    uv run python scripts/occupy_port.py 8080       # 占用指定端口
    uv run python scripts/occupy_port.py 8080 0.0.0.0  # 绑定所有网卡
"""
from __future__ import annotations

import argparse
import os
import socket
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="占用一个 TCP 端口，直到 Ctrl+C 退出")
    parser.add_argument("port", nargs="?", type=int, default=5173, help="要占用的端口号（默认 5173）")
    parser.add_argument("host", nargs="?", default="127.0.0.1", help="绑定地址（默认 127.0.0.1）")
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        print(f"error: 端口号必须在 1-65535 之间，收到 {args.port}", file=sys.stderr)
        return 1

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((args.host, args.port))
        sock.listen(1)
    except OSError as exc:
        print(f"error: 绑定 {args.host}:{args.port} 失败: {exc}", file=sys.stderr)
        return 1

    print(f"[占用中] {args.host}:{args.port}  (pid={os.getpid()})")
    print(f"  可用 DeskAgent 查询：system_query(category='port', port={args.port}, proto='tcp')")
    print("  按 Ctrl+C 释放端口退出")
    try:
        while True:
            # 接受连接但不处理，仅保持端口被占用；等待期间无连接时阻塞在 accept
            conn, _ = sock.accept()
            conn.close()
    except KeyboardInterrupt:
        print(f"\n[已释放] 端口 {args.port} 已关闭")
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
