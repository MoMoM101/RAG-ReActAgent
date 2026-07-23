"""Lifecycle monitor for optional OCR and reranker models.

Optional model work never gates core FastAPI readiness.  This monitor only
publishes slow/error state and resumes durable OCR-dependent documents after
the model becomes ready.
"""

from __future__ import annotations

import asyncio
import logging

from maintenance import get_maintenance_state
from sqlalchemy import update

from config import settings
from models.database import session_scope
from models.orm import DocStatus, Document

logger = logging.getLogger(__name__)


async def _annotate_waiting_documents(message: str) -> None:
    async with session_scope() as session:
        await session.execute(
            update(Document)
            .where(Document.status == DocStatus.waiting_for_ocr)
            .values(error_message=message[:500])
        )
        await session.commit()


async def monitor_optional_models() -> None:
    """Monitor forever; cancellation is handled by the app lifespan."""
    from ocr.factory import get_ocr_status

    from reranker.factory import get_reranker_status

    slow_logged: set[str] = set()
    ocr_ready_handled = False
    ocr_failure_notified = False

    while True:
        ocr = get_ocr_status()
        reranker = get_reranker_status()

        for name, status in (("OCR", ocr), ("Reranker", reranker)):
            if status.get("slow") and name not in slow_logged:
                slow_logged.add(name)
                logger.warning(
                    "%s optional model still running after %.0fs; continuing in background",
                    name,
                    status.get("elapsed_seconds", 0),
                )

        if not get_maintenance_state().active:
            if ocr["status"] == "ready" and not ocr_ready_handled:
                from rag.pipeline import resume_waiting_for_ocr_documents

                resumed = await resume_waiting_for_ocr_documents()
                ocr_ready_handled = True
                ocr_failure_notified = False
                if resumed:
                    logger.info("OCR ready; resumed %d waiting documents", resumed)
            elif ocr["status"] in ("downloading", "loading"):
                ocr_ready_handled = False
                ocr_failure_notified = False
            elif ocr["status"] in ("failed", "missing_dependency") and not ocr_failure_notified:
                await _annotate_waiting_documents(
                    f"{ocr['message']}。可执行 `{ocr['manual_command']}` 后重启服务重试。"
                )
                ocr_failure_notified = True

        await asyncio.sleep(settings.optional_model_poll_seconds)
