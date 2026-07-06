import cv2
import numpy as np


def preprocess(image: np.ndarray, dpi: int = 200) -> np.ndarray:
    """Preprocess image for OCR: grayscale -> denoise -> binarize.

    Args:
        image: BGR numpy array
        dpi: target DPI, image is upscaled if below this
    Returns:
        Preprocessed binary image
    """
    # Ensure minimum DPI by upscaling if needed
    h, w = image.shape[:2]
    min_pixels = dpi * 11  # ~11 inches for A4
    if max(h, w) < min_pixels:
        scale = min_pixels / max(h, w)
        image = cv2.resize(image, (int(w * scale), int(h * scale)))

    # Grayscale
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    # Denoise
    denoised = cv2.fastNlMeansDenoising(gray, h=10)

    # Contrast enhancement (CLAHE) — helps Chinese character strokes
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)

    # Adaptive threshold binarization
    binary = cv2.adaptiveThreshold(
        enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 3
    )

    return binary


def image_from_bytes(data: bytes, dpi: int = 200) -> np.ndarray:
    """Load image from bytes and preprocess for OCR."""
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return preprocess(img, dpi=dpi)
