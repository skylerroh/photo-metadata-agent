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
        image = base64.b64encode(request.image).decode()
        text = json.dumps({"album_context": request.context, "questions": request.questions})
        messages = [
            {
                "role": "system",
                "content": (
                    request.prompt + "\nReturn only JSON matching the supplied output schema."
                ),
            }
        ]
        if self.provider == "ollama":
            messages.append({"role": "user", "content": text, "images": [image]})
            body = {
                "model": request.model,
                "messages": messages,
                "format": Tags.model_json_schema(),
                "stream": False,
            }
            headers = {}
        else:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image}"},
                        },
                    ],
                }
            )
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
