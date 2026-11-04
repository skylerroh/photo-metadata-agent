from photo_metadata_agent.domain.schemas import ContextTags, Prediction, Tags
from photo_metadata_agent.inference.contracts import TagRequest


class DemoTagger:
    """No inference: unknown values let the entire review flow run without API keys."""

    def tag(self, request: TagRequest) -> Prediction:
        return Prediction(
            tags=Tags(
                people_count_bucket="unknown",
                capture_style="unknown",
                contexts=ContextTags(event=None, ministry_trip=None, family=None),
            ),
            resolved_model="demo-v1",
            raw={"demo": True, "message": "No inference performed. Annotate this image manually."},
        )
