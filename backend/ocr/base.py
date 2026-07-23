from abc import ABC, abstractmethod


class BaseOCR(ABC):
    @abstractmethod
    def recognize(self, image) -> str:
        """Recognize text from an image (numpy array or PIL Image).
        Returns extracted text string.
        """
        ...

    def recognize_from_bytes(self, data: bytes, dpi: int = 200) -> str:
        """Recognize text from image bytes. Override in subclasses that support this."""
        return ""
