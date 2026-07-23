"""Pre-download optional OCR and reranker models.

Run from ``backend``:
    python -m tools.download_models --ocr --reranker

The command waits for completion but imposes no total download deadline.
Ctrl+C only stops this command; partial caches are reused on the next run.
"""

from __future__ import annotations

import argparse
import os
import time

from config import settings

TERMINAL = {"ready", "failed", "missing_dependency", "disabled"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载并预热 RAG Agent 可选模型")
    parser.add_argument("--ocr", action="store_true", help="下载并预热 OCR 模型")
    parser.add_argument("--reranker", action="store_true", help="下载并预热 Reranker 模型")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    want_ocr = args.ocr or not (args.ocr or args.reranker)
    want_reranker = args.reranker or not (args.ocr or args.reranker)

    if settings.hf_endpoint:
        os.environ["HF_ENDPOINT"] = settings.hf_endpoint

    readers = {}
    if want_ocr:
        settings.ocr_enabled = True
        from ocr.factory import get_ocr_status, preload_ocr_async

        preload_ocr_async()
        readers["OCR"] = get_ocr_status
    if want_reranker:
        settings.rerank_enabled = True
        from reranker.factory import get_reranker_status, preload_reranker_async

        preload_reranker_async()
        readers["Reranker"] = get_reranker_status

    last_messages: dict[str, str] = {}
    try:
        while True:
            statuses = {name: reader() for name, reader in readers.items()}
            for name, status in statuses.items():
                message = f"[{name}] {status['status']}: {status['message']}"
                if status.get("last_error"):
                    message += f" ({status['last_error']})"
                if last_messages.get(name) != message:
                    print(message, flush=True)
                    last_messages[name] = message

            if all(status["status"] in TERMINAL for status in statuses.values()):
                return 0 if all(status["status"] == "ready" for status in statuses.values()) else 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已停止等待；核心服务不受影响，下次运行会复用已有缓存。", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
