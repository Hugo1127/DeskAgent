from __future__ import annotations

import datetime
from datetime import UTC


# 全项目统一的时间戳入口：返回当前 UTC 时间的 ISO 8601 字符串。
# 各模块原各自维护一份 `_now()`，现收敛到此处，避免某处忘记 UTC 导致时间 bug。
def now_utc() -> str:
    return datetime.datetime.now(UTC).isoformat()
