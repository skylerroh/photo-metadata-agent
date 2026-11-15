import base64
import json

from pydantic import ValidationError

from photo_metadata_agent.domain.schemas import Prediction, Tags
from photo_metadata_agent.inference.contracts import ProviderError, TagRequest
from photo_metadata_agent.inference.providers.http import post_json


class ChatTagger:
    """OpenAI/OpenRouter Chat Completions and native Ollama adapter."""

    def __init__(self, provider: str, url: str, key: str, timeout: float, attempts: int):
        self.provider, self.url, self.key = provider, url, key
        self.timeout, self.attempts = timeout, attempts

    def tag(self, request: TagRequest) -> Prediction:
        if request.video is not None and self.provider != "openrouter":
            raise ProviderError("This adapter accepts sampled frames, not native video.")
        images = [base64.b64encode(image).decode() for image in request.images()]
        text = json.dumps(
            {
                "album_context": request.context,
                "questions": request.questions,
                "media": request.media_context,
            }
        )
        messages = [
            {
                "role": "system",
                "content": (
                    request.prompt + "\nReturn only JSON matching the supplied output schema."
                ),
            }
        ]
        if self.provider == "ollama":
            messages.append({"role": "user", "content": text, "images": images})
            body = {
                "model": request.model,
                "messages": messages,
                "format": Tags.model_json_schema(),
                "stream": False,
            }
            headers = {}
        else:
            parts = [{"type": "text", "text": text}]
            if request.video is not None:
                video = base64.b64encode(request.video).decode()
                parts.append(
                    {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{video}"}}
                )
            else:
                for index, image in enumerate(images):
                    if request.frames:
                        parts.append(
                            {
                                "type": "text",
                                "text": (
                                    f"Frame {index + 1} at {request.frames[index][0]:.3f} seconds"
                                ),
                            }
                        )
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image}"},
                        }
                    )
            messages.append({"role": "user", "content": parts})
            body = {
                "model": request.model,
                "messages": messages,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "photo_tags",
                        "strict": True,
                        "schema": Tags.model_json_schema(),
                    },
                },
            }
            if self.provider == "openrouter":
                body["provider"] = {"require_parameters": True}
            headers = {"Authorization": f"Bearer {self.key}"}
        raw = post_json(self.url, body, headers, self.timeout, self.attempts)
        try:
            if self.provider == "ollama":
                if raw.get("done") is not True:
                    raise ProviderError("Provider did not finish its response.")
                content = raw["message"]["content"]
            else:
                choice = raw["choices"][0]
                if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
                    raise ProviderError("Provider refused or did not finish its response.")
                content = choice["message"]["content"]
            return Prediction(
                tags=Tags.model_validate_json(content),
                raw=raw,
                resolved_model=raw.get("model", request.model),
            )
        except (KeyError, IndexError, TypeError, ValidationError) as exc:
            raise ProviderError("Provider response did not match the tagging schema.") from exc
