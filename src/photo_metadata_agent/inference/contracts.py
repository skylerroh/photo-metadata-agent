from dataclasses import dataclass
from typing import Protocol

from photo_metadata_agent.domain.schemas import Prediction


@dataclass(frozen=True)
class TagRequest:
    image: bytes
    context: str
    questions: dict
    prompt: str
    model: str


class Tagger(Protocol):
    def tag(self, request: TagRequest) -> Prediction: ...


class ProviderError(RuntimeError):
    """Safe-to-display error: never include credentials, image bytes, or response bodies."""
