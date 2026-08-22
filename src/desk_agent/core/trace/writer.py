from __future__ import annotations

import asyncio
from pathlib import Path

from desk_agent.core.trace.record import TraceRecord


class TraceWriter:
    # 初始化 TraceWriter；写入目标文件路径在 start() 前不会创建
    def __init__(self, path: Path) -> None:
        self._path = path
        self._queue: asyncio.Queue[TraceRecord] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    # 创建目录、启动后台 drain task
    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._drain())

    # 等待队列清空后取消 drain task；各步限时，避免关闭流程被写队列卡死
    async def stop(self) -> None:
        try:
            await asyncio.wait_for(self._queue.join(), timeout=2.0)
        except (TimeoutError, asyncio.CancelledError):
            pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # 非阻塞地将 record 放入写入队列
    def emit(self, record: TraceRecord) -> None:
        self._queue.put_nowait(record)

    # 持续从队列读取 record 并追加写入文件（显式 UTF-8，避免 Windows 默认编码写 emoji 等字符报错）
    async def _drain(self) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            while True:
                record = await self._queue.get()
                try:
                    f.write(record.model_dump_json() + "\n")
                    f.flush()
                finally:
                    self._queue.task_done()
