import os
import threading
import time
from .base import BaseOCR


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
            url = _rewrite_url(url)

            fname = url.rpartition("/")[-1].split("&")[0]
            if fname.startswith("https:"):
                fname = fname.rpartition("/")[-1]
            print(f"[OCR] 下载中: {fname}", flush=True)

            with open(filename, "wb") as fh:
                req = urllib.request.Request(url, headers={"User-Agent": _data.USER_AGENT})
                with urllib.request.urlopen(req, timeout=120) as response:
                    total = response.length or 0
                    downloaded = 0
                    last_milestone = -10
                    t_start = time.time()
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
                                speed = downloaded / elapsed / 1024 if elapsed > 0 else 0
                                eta = (total - downloaded) / (speed * 1024) if speed > 0 else 0
                                print(f"[OCR]   进度 {pct}%  {speed:.0f}KB/s  ETA {eta:.0f}s", flush=True)

            print(f"[OCR] 下载完成: {fname}", flush=True)

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
        print(f"[OCR] 正在检查模型...", flush=True)

        def _download():
            try:
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
            except Exception as e:
                print(f"[OCR] 加载失败: {e}", flush=True)

        threading.Thread(target=_download, daemon=True).start()

    @property
    def ready(self) -> bool:
        return self._ready

    def recognize(self, image) -> str:
        if not self._ready or self._model is None:
            return ""
        import numpy as np

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
