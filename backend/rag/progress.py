"""Progress manager for document processing — publish/subscribe via asyncio.Queue."""

import asyncio
import logging
from contextlib import suppress

logger = logging.getLogger(__name__)


class ProgressManager:
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def publish(self, doc_id: str, event: dict) -> None:
        """推送事件到该文档的所有订阅者。

        Uses a bounded queue to avoid memory leaks from slow consumers.
        If the queue is full, older non-terminal events are discarded to
        make room for the new event.
        """
        queues = self._subscribers.get(doc_id, [])
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # If this is a terminal event, drain one old event to make room.
                # For non-terminal events, just drop with a warning.
                is_terminal = event.get("status") in ("completed", "failed")
                if is_terminal:
                    with suppress(asyncio.QueueEmpty):
                        q.get_nowait()
                    q.put_nowait(event)
                else:
                    logger.warning(
                        "progress queue full for doc=%s, dropping event: %s",
                        doc_id, event.get("status"),
                    )

    async def subscribe(self, doc_id: str) -> asyncio.Queue:
        """订阅文档进度。返回一个 Queue，容量足以容纳大量文档事件。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.setdefault(doc_id, []).append(q)
        return q

    def unsubscribe(self, doc_id: str, q: asyncio.Queue) -> None:
        """取消订阅并清理。"""
        queues = self._subscribers.get(doc_id, [])
        if q in queues:
            queues.remove(q)
        if not queues:
            self._subscribers.pop(doc_id, None)


# 全局单例
progress = ProgressManager()
