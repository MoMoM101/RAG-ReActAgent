"""Progress manager for document processing — publish/subscribe via asyncio.Queue."""

import asyncio


class ProgressManager:
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def publish(self, doc_id: str, event: dict) -> None:
        """推送事件到该文档的所有订阅者。"""
        queues = self._subscribers.get(doc_id, [])
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # 订阅者没在消费，跳过

    async def subscribe(self, doc_id: str) -> asyncio.Queue:
        """订阅文档进度。返回一个 Queue。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
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
