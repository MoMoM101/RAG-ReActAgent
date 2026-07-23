"""Progress manager for document processing — publish/subscribe via asyncio.Queue."""

import asyncio
import logging
from contextlib import suppress

logger = logging.getLogger(__name__)


class ProgressManager:
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._latest: dict[str, dict] = {}  # last event per doc_id for late joiners

    def publish(self, doc_id: str, event: dict) -> None:
        """推送事件到该文档的所有订阅者，并缓存最新事件供延迟订阅者重放。"""
        self._latest[doc_id] = event
        queues = self._subscribers.get(doc_id, [])
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
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
        """订阅文档进度。新订阅者立即收到最新缓存事件（如果存在）。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.setdefault(doc_id, []).append(q)
        # Replay the latest event so late subscribers don't start with an
        # empty queue when the task has already begun publishing.
        latest = self._latest.get(doc_id)
        if latest is not None:
            with suppress(asyncio.QueueFull):
                q.put_nowait(latest)
        return q

    def unsubscribe(self, doc_id: str, q: asyncio.Queue) -> None:
        """取消订阅并清理。"""
        queues = self._subscribers.get(doc_id, [])
        if q in queues:
            queues.remove(q)
        if not queues:
            self._subscribers.pop(doc_id, None)
            # Keep _latest[doc_id] for potential re-subscription


# 全局单例
progress = ProgressManager()
