"""daemon 运行文件（run file）读写。

desk-core 启动后把令牌、端口、PID、证书指纹写入 `~/.desk/run/daemon.json`，
客户端（CLI / TUI）连接前读取该文件获取连接信息。

安全要点（见 docs/COMMUNICATION_SECURITY.md）：
- token 每次 daemon 启动随机生成（secrets.token_urlsafe(32)，256-bit）；
- POSIX 下文件 chmod 0600，Windows 依赖用户目录 ACL；
- token 绝不写入日志 / trace，只落盘本文件。
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

_RUN_DIR = Path.home() / ".desk" / "run"
_DAEMON_FILE = "daemon.json"
_CERT_FILE = "desk.crt"
_KEY_FILE = "desk.key"

# 旧版 pid 文件（desk core stop/status 兼容读取）
_LEGACY_PID_FILE = Path.home() / ".desk" / "desk-core.pid"


@dataclass
class DaemonInfo:
    token: str
    port: int
    pid: int
    started_at: str
    version: str
    cert_sha256: str | None = None


# 生成一个新的 256-bit 随机令牌（每次 daemon 启动轮换）
def new_token() -> str:
    return secrets.token_urlsafe(32)


def run_dir() -> Path:
    return _RUN_DIR


def daemon_file_path() -> Path:
    return _RUN_DIR / _DAEMON_FILE


def cert_file_path() -> Path:
    return _RUN_DIR / _CERT_FILE


def key_file_path() -> Path:
    return _RUN_DIR / _KEY_FILE


# 写入 daemon.json；run_dir 参数仅测试用
def write_daemon_file(info: DaemonInfo, *, run_dir: Path | None = None) -> None:
    directory = run_dir or _RUN_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _DAEMON_FILE
    data = {
        "token": info.token,
        "port": info.port,
        "pid": info.pid,
        "started_at": info.started_at,
        "version": info.version,
        "cert_sha256": info.cert_sha256,
    }
    # 先写临时文件再原子替换，避免客户端读到半截 JSON
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    tmp.replace(path)
    if os.name != "nt":
        os.chmod(path, 0o600)


# 读取 daemon.json；文件缺失或损坏（daemon 正在写入/已清理）时返回 None
def read_daemon_file(*, run_dir: Path | None = None) -> DaemonInfo | None:
    path = (run_dir or _RUN_DIR) / _DAEMON_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DaemonInfo(
            token=str(data["token"]),
            port=int(data["port"]),
            pid=int(data["pid"]),
            started_at=str(data.get("started_at", "")),
            version=str(data.get("version", "")),
            cert_sha256=data.get("cert_sha256"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


# 删除 daemon.json（daemon 正常退出时清理）
def remove_daemon_file(*, run_dir: Path | None = None) -> None:
    path = (run_dir or _RUN_DIR) / _DAEMON_FILE
    path.unlink(missing_ok=True)


# 读取 TLS 自签证书 PEM 内容（供客户端 pinning），缺失时返回 None
def read_cert_pem(*, run_dir: Path | None = None) -> str | None:
    path = (run_dir or _RUN_DIR) / _CERT_FILE
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


# 检查进程是否存活（Windows 下 os.kill(pid, 0) 亦可用作存在性探测）
def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


# 返回正在运行的 daemon PID：优先 run 文件，回退旧版 pid 文件；无则 None
def running_pid() -> int | None:
    info = read_daemon_file()
    if info is not None and pid_alive(info.pid):
        return info.pid
    if _LEGACY_PID_FILE.exists():
        try:
            pid = int(_LEGACY_PID_FILE.read_text(encoding="utf-8").strip())
            if pid_alive(pid):
                return pid
            _LEGACY_PID_FILE.unlink(missing_ok=True)
        except (ValueError, OSError):
            _LEGACY_PID_FILE.unlink(missing_ok=True)
    return None


# 清理旧版 pid 文件（daemon 正常退出时一并删除）
def remove_legacy_pid_file() -> None:
    _LEGACY_PID_FILE.unlink(missing_ok=True)
