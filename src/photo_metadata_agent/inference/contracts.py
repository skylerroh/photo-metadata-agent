from dataclasses import dataclass
from typing import Protocol

from photo_metadata_agent.domain.schemas import Prediction


@dataclass(frozen=True)
class TagRequest:
    image: bytes | None
    context: str
    questions: dict
    prompt: str
    model: str
    frames: tuple[tuple[float, bytes], ...] = ()
    video: bytes | None = None
    media_context: dict | None = None

    def __post_init__(self):
        if sum((self.image is not None, bool(self.frames), self.video is not None)) != 1:
            raise ValueError("Supply exactly one image, frame sequence, or native video.")

    def images(self) -> tuple[bytes, ...]:
        if self.frames:
            return tuple(content for _, content in self.frames)
        return (self.image,) if self.image is not None else ()


class Tagger(Protocol):
    def tag(self, request: TagRequest) -> Prediction: ...


class ProviderError(RuntimeError):
    """Safe-to-display error: never include credentials, image bytes, or response bodies."""
