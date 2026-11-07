import hashlib
import json
from importlib.resources import files
from pathlib import Path

from photo_metadata_agent.ingestion.local import ingest
from photo_metadata_agent.settings import Settings
from photo_metadata_agent.storage.repository import Repository


def make_spec(settings: Settings, context: str = "", questions_path: Path | None = None) -> dict:
    package = files("photo_metadata_agent")
    question_text = (
        questions_path.read_text()
        if questions_path
        else package.joinpath("resources/questions.json").read_text()
    )
    questions = json.loads(question_text)
    # Reject taxonomy shape changes until the corresponding domain contract is updated.
    from photo_metadata_agent.domain.schemas import ContextTags, Tags

    schema = Tags.model_json_schema()
    for name in ("people_count_bucket", "capture_style"):
        if set(questions[name]["criteria"]) != set(schema["properties"][name]["enum"]):
            raise ValueError(f"Question criteria for {name} do not match the domain schema.")
    if set(questions["contexts"]) != set(ContextTags.model_fields):
        raise ValueError("Context questions do not match the domain schema.")
    prompt = package.joinpath("prompts/tagging.txt").read_text()
    spec = settings.model_dump(mode="json", exclude={"data_dir", "max_upload_bytes"})
    spec.update(
        {
            "context": context,
            "questions": questions,
            "prompt": prompt,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "questions_sha256": hashlib.sha256(question_text.encode()).hexdigest(),
            "schema_version": "1",
            "harness_version": "0.1.0",
            "preprocessing_version": "jpeg-v1",
        }
    )
    return spec


def import_image(
    content: bytes,
    filename: str,
    source: str,
    settings: Settings,
    repository: Repository,
) -> str:
    if len(content) > settings.max_upload_bytes:
        raise ValueError("Image exceeds the configured upload limit.")
    asset_id = ingest(content, filename, settings.data_dir)
    repository.add_asset(asset_id, Path(filename).name, source)
    return asset_id
