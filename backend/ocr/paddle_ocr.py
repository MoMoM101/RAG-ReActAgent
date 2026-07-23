import os
import threading
import time
from pathlib import Path

from .base import BaseOCR


def _models_cached() -> bool:
    model_dir = Path(__file__).resolve().parent.parent / "data" / ".doctr" / "models"
    return (
        any(model_dir.glob("db_resnet50*.pt"))
        and any(model_dir.glob("crnn_mobilenet_v3_large*.pt"))
    )


def _patch_doctr_download():
    """Patch doctr's download to use GitHub mirror and clean single-line progress."""
    try:
        import re
        import urllib.request

        import doctr.utils.data as _data

        _gh_mirror = "https://gh.idayer.com"

        def _rewrite_url(url: str) -> str:
            """Rewrite doctr CDN URLs → github.com → gh-proxy mirror, since github is blocked in CN."""
            # doctr CDN pattern: https://doctr-static.mindee.com/models?id=<version>/<file>.pt&src=0
            # which 302-redirects to: https://github.com/mindee/doctr/releases/download/<version>/<file>.pt
            m = re.search(r"[?&]id=([^&]+)", url)
            if m:
                path = m.group(1)  # e.g. v0.7.0/db_resnet50-79bd7d70.pt
                gh_url = f"https://github.com/mindee/doctr/releases/download/{path}"
                return f"{_gh_mirror}/{gh_url}"
            # If already a github URL (e.g. from HF hub), also proxy it
            if "github.com" in url:
                return f"{_gh_mirror}/{url}"
            return url

        def _patched_urlretrieve(url: str, filename, chunk_size: int = 1024 * 1024) -> None:
            original_url = url
            rewritten_url = _rewrite_url(url)
            urls = [rewritten_url]
            if rewritten_url != original_url:
                urls.append(original_url)

            fname = rewritten_url.rpartition("/")[-1].split("&")[0]
            if fname.startswith("https:"):
                fname = fname.rpartition("/")[-1]
            print(f"[OCR] 下载中: {fname}", flush=True)
            part_path = Path(f"{filename}.part")
            last_error: Exception | None = None
            for candidate_url in urls:
                try:
                    existing = part_path.stat().st_size if part_path.exists() else 0
                    headers = {"User-Agent": _data.USER_AGENT}
                    if existing:
                        headers["Range"] = f"bytes={existing}-"
                    req = urllib.request.Request(candidate_url, headers=headers)
                    with urllib.request.urlopen(req, timeout=120) as response:
                        resumed = existing > 0 and getattr(response, "status", 200) == 206
                        if existing and not resumed:
                            existing = 0
                        response_size = response.length or 0
                        total = existing + response_size if response_size else 0
                        downloaded = existing
                        last_milestone = downloaded * 100 // total // 10 * 10 - 10 if total else -10
                        t_start = time.time()
                        with part_path.open("ab" if resumed else "wb") as fh:
                            for chunk in iter(lambda: response.read(chunk_size), b""):
                                if not chunk:
                                    break
                                fh.write(chunk)
                                downloaded += len(chunk)
                                if total > 0:
                                    pct = downloaded * 100 // total
                                    milestone = pct // 10 * 10
                                    if milestone > last_milestone:
                                        last_milestone = milestone
                                        elapsed = time.time() - t_start
                                        transferred = max(0, downloaded - existing)
                                        speed = transferred / elapsed / 1024 if elapsed > 0 else 0
                                        eta = (total - downloaded) / (speed * 1024) if speed > 0 else 0
                                        print(
                                            f"[OCR]   进度 {pct}%  {speed:.0f}KB/s  ETA {eta:.0f}s",
                                            flush=True,
                                        )
                    part_path.replace(filename)
                    print(f"[OCR] 下载完成: {fname}", flush=True)
                    return
                except Exception as exc:
                    last_error = exc
                    print(f"[OCR] 下载源失败，尝试下一个地址: {exc}", flush=True)
            if last_error is not None:
                raise last_error

        _data._urlretrieve = _patched_urlretrieve
    except Exception:
        pass  # best-effort patch


class DoctrOCREngine(BaseOCR):
    def __init__(self):
        from config import settings
        self._model = None
        self._ready = False

        # Route HF downloads through mirror
        if settings.hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)

    def preload_async(self):
        """Check and download models in background."""
        print("[OCR] 正在检查模型...", flush=True)

        def _download():
            try:
                cached = _models_cached()
                from ocr.factory import set_ocr_phase
                set_ocr_phase("loading" if cached else "downloading", cached=cached)
                _patch_doctr_download()
                os.environ["DOCTR_CACHE_DIR"] = os.path.join(
                    os.path.dirname(__file__), "..", "data", ".doctr"
                )
                start = time.time()
                # doctr loads models from HF mirror; db_resnet50 detects text well for all languages
                # crnn_mobilenet_v3_large is lighter and handles variable-length text better
                from doctr.models import ocr_predictor
                self._model = ocr_predictor(
                    det_arch="db_resnet50",
                    reco_arch="crnn_mobilenet_v3_large",
                    pretrained=True,
                )
                self._ready = True
                elapsed = time.time() - start
                print(f"[OCR] 模型就绪，耗时 {elapsed:.0f}s", flush=True)
                from ocr.factory import set_ocr_ready
                set_ocr_ready()
            except Exception as e:
                print(f"[OCR] 加载失败: {e}", flush=True)
                self._ready = False
                from ocr.factory import set_ocr_failed
                set_ocr_failed(str(e))

        threading.Thread(target=_download, daemon=True).start()

    @property
    def ready(self) -> bool:
        return self._ready

    def recognize(self, image) -> str:
        if not self._ready or self._model is None:
            return ""

        # doctr expects RGB numpy array
        if len(image.shape) == 3 and image.shape[2] == 3:
            rgb = image
        else:
            import cv2
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        result = self._model([rgb])
        # Extract text from result
        page = result.pages[0]
        lines = []
        for block in page.blocks:
            for line in block.lines:
                text = " ".join(word.value for word in line.words)
                if text.strip():
                    lines.append(text)
        return "\n".join(lines)

    def recognize_from_bytes(self, data: bytes, dpi: int = 200) -> str:
        import cv2
        import numpy as np

        from ocr.preprocess import preprocess
        arr = np.frombuffer(data, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            img = preprocess(img, dpi=dpi)
            return self.recognize(img)
        return ""
