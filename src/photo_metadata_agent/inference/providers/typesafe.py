import base64
import math
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from photo_metadata_agent.domain.schemas import Prediction, Tags
from photo_metadata_agent.inference.contracts import ProviderError, TagRequest
from photo_metadata_agent.inference.providers.http import post_json


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(strict=True)
    choice: str
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float]


def build_questions(spec: dict) -> dict:
    questions = {
        field: {"type": "choice", **spec[field]}
        for field in ("people_count_bucket", "capture_style")
    }
    for field, instruction in spec["contexts"].items():
        questions[f"context_{field}"] = {
            "type": "choice",
            "instructions": instruction,
            "criteria": {
                "yes": "Evidence establishes this context.",
                "no": "Evidence explicitly establishes that this context does not apply.",
                "unknown": "Insufficient evidence to determine whether this context applies.",
            },
        }
    return questions


class TypeSafeTagger:
    def __init__(self, key: str, timeout: float, attempts: int):
        self.key, self.timeout, self.attempts = key, timeout, attempts

    def tag(self, request: TagRequest) -> Prediction:
        reference = str(uuid4())
        questions = build_questions(request.questions)
        # The same policy applies to every atomic question, not just the chat adapters.
        for question in questions.values():
            question["instructions"] = request.prompt + "\n" + question["instructions"]
        body = {
            "model": request.model,
            "state": {"photo": reference, "album_context": request.context},
            "assets": {
                reference: {
                    "type": "image",
                    "format": "jpg",
                    "data_base64": base64.b64encode(request.image).decode(),
                }
            },
            "questions": questions,
        }
        raw = post_json(
            "https://api.typesafe.ai/v1/systemone",
            body,
            {"Authorization": f"Bearer {self.key}", "TypeSafe-Danger-Zone-Images": "true"},
            self.timeout,
            self.attempts,
        )
        try:
            answers = {
                field: ChoiceAnswer.model_validate(raw["answers"][field]) for field in questions
            }
            for field, answer in answers.items():
                if answer.choice not in questions[field]["criteria"]:
                    raise ValueError("Unknown choice")
                if set(answer.probabilities) != set(questions[field]["criteria"]):
                    raise ValueError("Incomplete probabilities")
                if not math.isclose(sum(answer.probabilities.values()), 1.0, abs_tol=0.01):
                    raise ValueError("Invalid probability sum")
                if any(not 0 <= p <= 1 for p in answer.probabilities.values()):
                    raise ValueError("Invalid probabilities")
            contexts = {
                field: {"yes": True, "no": False, "unknown": None}[
                    answers[f"context_{field}"].choice
                ]
                for field in request.questions["contexts"]
            }
            tags = Tags.model_validate(
                {
                    "people_count_bucket": answers["people_count_bucket"].choice,
                    "capture_style": answers["capture_style"].choice,
                    "contexts": contexts,
                }
            )
            return Prediction(
                tags=tags,
                scores={k: v.confidence for k, v in answers.items()},
                raw=raw,
                resolved_model=raw["model"],
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderError("Jev response did not match the tagging questions.") from exc
