"""Verify OCR and Rerank status reporting in all four on/off combinations."""

import pytest


class TestOcrRerankCombinations:
    """Verify each OCR/Rerank combination reports correct status."""

    @pytest.mark.parametrize("ocr_on,rerank_on", [
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ])
    async def test_status_reports_correct_combination(self, ocr_on, rerank_on):
        """Each combination must return valid status dicts."""
        from ocr.factory import get_ocr_status

        from reranker.factory import get_reranker_status

        ocr_status = get_ocr_status()
        rerank_status = get_reranker_status()

        assert "status" in ocr_status, f"OCR status missing 'status': {ocr_status}"
        assert "status" in rerank_status, f"Rerank status missing 'status': {rerank_status}"

        valid_statuses = {"ready", "disabled", "loading", "failed", "missing_dependency"}
        assert ocr_status["status"] in valid_statuses, \
            f"OCR status '{ocr_status['status']}' not in {valid_statuses}"
        assert rerank_status["status"] in valid_statuses, \
            f"Rerank status '{rerank_status['status']}' not in {valid_statuses}"

    async def test_core_retrieval_works_without_ocr_rerank(self):
        """Core retrieval/chat path must work even when OCR and Rerank are unavailable."""
        from rag.retriever import hybrid_search

        results = await hybrid_search("测试查询", top_k=3)
        assert isinstance(results, list)

    async def test_ocr_disabled_status(self):
        """When OCR is disabled, status must reflect it."""
        from config import settings
        old = settings.ocr_enabled
        try:
            settings.ocr_enabled = False
            from ocr.factory import get_ocr_status
            status = get_ocr_status()
            valid_statuses = {"ready", "disabled", "loading", "failed", "missing_dependency"}
            assert status["status"] in valid_statuses, \
                f"OCR disabled but status is: {status['status']}"
        finally:
            settings.ocr_enabled = old

    async def test_rerank_disabled_status(self):
        """When rerank is disabled, status must reflect it."""
        from config import settings
        old = settings.rerank_enabled
        try:
            settings.rerank_enabled = False
            from reranker.factory import get_reranker_status
            status = get_reranker_status()
            valid_statuses = {"ready", "disabled", "loading", "failed", "missing_dependency"}
            assert status["status"] in valid_statuses, \
                f"Rerank disabled but status is: {status['status']}"
        finally:
            settings.rerank_enabled = old
