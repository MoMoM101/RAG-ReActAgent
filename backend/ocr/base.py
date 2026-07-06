from abc import ABC, abstractmethod


class BaseOCR(ABC):
    @abstractmethod
    def recognize(self, image) -> str:
        """Recognize text from an image (numpy array or PIL Image).
        Returns extracted text string.
        """
        ...
