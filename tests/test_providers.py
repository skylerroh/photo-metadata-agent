import json

import httpx
import pytest

from photo_metadata_agent.inference.contracts import ProviderError, TagRequest
from photo_metadata_agent.inference.factory import create_tagger
from photo_metadata_agent.inference.providers.chat import ChatTagger
from photo_metadata_agent.inference.providers.http import post_json
from photo_metadata_agent.inference.providers.typesafe import TypeSafeTagger, build_questions
from photo_metadata_agent.services.catalog import make_spec


def request(settings, image_bytes):
    spec = make_spec(settings)
    return TagRequest(image_bytes, "Family picnic", spec["questions"], spec["prompt"], "test-model")


@pytest.mark.parametrize("provider", ["openai", "openrouter", "ollama"])
def test_chat_adapters_send_vision_and_schema(
    provider, settings, image_bytes, review_body, monkeypatch
):
    captured = {}

    def fake(url, body, headers, timeout, attempts):
        captured.update(body)
        content = json.dumps(review_body["tags"])
        if provider == "ollama":
            return {"done": True, "message": {"content": content}, "model": "resolved"}
        return {
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "model": "resolved",
        }

    monkeypatch.setattr("photo_metadata_agent.inference.providers.chat.post_json", fake)
    tagger = ChatTagger(provider, "http://example.test", "secret", 10, 1)
    prediction = tagger.tag(request(settings, image_bytes))
    assert prediction.tags.model_dump() == review_body["tags"]
    assert prediction.resolved_model == "resolved"
    if provider == "ollama":
        assert captured["messages"][1]["images"]
        assert captured["format"]["additionalProperties"] is False
    else:
        assert captured["messages"][1]["content"][1]["type"] == "image_url"
        assert captured["response_format"]["json_schema"]["strict"] is True


def test_jev_normalizes_unknown_and_preserves_distributions(settings, image_bytes, monkeypatch):
    captured = {}
    spec = make_spec(settings)

    def fake(url, body, headers, timeout, attempts):
        captured.update({"body": body, "headers": headers})
        return {
            "model": "jev-1.14.0",
            "answers": {
                name: {
                    "choice": "unknown",
                    "confidence": 0.9,
                    "probabilities": {
                        key: 1.0 if key == "unknown" else 0.0 for key in question["criteria"]
                    },
                }
                for name, question in build_questions(spec["questions"]).items()
            },
        }

    monkeypatch.setattr("photo_metadata_agent.inference.providers.typesafe.post_json", fake)
    prediction = TypeSafeTagger("secret", 10, 1).tag(request(settings, image_bytes))
    assert prediction.tags.contexts.family is None
    assert prediction.scores["people_count_bucket"] == 0.9
    assert prediction.raw["answers"]["context_family"]["probabilities"]
    assert captured["headers"]["TypeSafe-Danger-Zone-Images"] == "true"
    assert len(captured["body"]["assets"]) == 1
    assert captured["body"]["state"]["photo"] in captured["body"]["assets"]


@pytest.mark.parametrize("content", ['{"people_count_bucket":"500"}', "not-json"])
def test_invalid_output_is_not_silently_accepted(content, settings, image_bytes, monkeypatch):
    monkeypatch.setattr(
        "photo_metadata_agent.inference.providers.chat.post_json",
        lambda *a: {
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        },
    )
    with pytest.raises(ProviderError, match="schema"):
        ChatTagger("openai", "http://example.test", "secret", 10, 1).tag(
            request(settings, image_bytes)
        )


def test_jev_requires_explicit_image_opt_in(settings):
    spec = make_spec(settings)
    spec["provider"] = "typesafe"
    with pytest.raises(ProviderError, match="Enable"):
        create_tagger(spec)


def test_transport_retries_transient_failure_without_exposing_body(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, json={"ok": True})

    real_client = httpx.Client
    monkeypatch.setattr("photo_metadata_agent.inference.providers.http.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "photo_metadata_agent.inference.providers.http.httpx.Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    assert post_json("http://example.test", {}, {}, 10, 2) == {"ok": True}
    assert len(calls) == 2


def test_transport_does_not_retry_authentication_error(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, text="secret response content")

    real_client = httpx.Client
    monkeypatch.setattr(
        "photo_metadata_agent.inference.providers.http.httpx.Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    with pytest.raises(ProviderError) as error:
        post_json("http://example.test", {}, {}, 10, 2)
    assert str(error.value) == "Provider returned HTTP 401."
    assert len(calls) == 1


def test_committed_schema_matches_contract():
    from pathlib import Path

    from photo_metadata_agent.domain.schemas import Tags

    assert (
        json.loads(Path("configs/schemas/tags.schema.json").read_text()) == Tags.model_json_schema()
    )


@pytest.mark.parametrize("provider", ["openai", "openrouter", "ollama"])
def test_frame_sequence_is_sent_in_one_request(
    provider,
    settings,
    image_bytes,
    review_body,
    monkeypatch,
):
    captured = {}

    def fake(url, body, headers, timeout, attempts):
        captured.update(body)
        message = {"content": json.dumps(review_body["tags"])}
        if provider == "ollama":
            return {"done": True, "message": message}
        return {"choices": [{"finish_reason": "stop", "message": message}]}

    monkeypatch.setattr("photo_metadata_agent.inference.providers.chat.post_json", fake)
    spec = make_spec(settings)
    request = TagRequest(
        None,
        "",
        spec["questions"],
        spec["prompt"],
        "test-model",
        frames=((0.5, image_bytes), (10.5, image_bytes)),
        media_context={"mode": "frames", "frame_timestamps_seconds": [0.5, 10.5]},
    )
    ChatTagger(provider, "http://example.test", "secret", 10, 1).tag(request)
    if provider == "ollama":
        assert len(captured["messages"][1]["images"]) == 2
        assert "10.5" in captured["messages"][1]["content"]
    else:
        parts = captured["messages"][1]["content"]
        assert len([p for p in parts if p["type"] == "image_url"]) == 2
        assert any("10.500 seconds" in p.get("text", "") for p in parts)
        assert not any(p["type"] == "video_url" for p in parts)


def test_openrouter_native_video_uses_private_data_uri(settings, review_body, monkeypatch):
    captured = {}

    def fake(url, body, *args):
        captured.update(body)
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(review_body["tags"]),
                    },
                }
            ]
        }

    monkeypatch.setattr("photo_metadata_agent.inference.providers.chat.post_json", fake)
    spec = make_spec(settings)
    request = TagRequest(None, "", spec["questions"], spec["prompt"], "test-model", video=b"mp4")
    ChatTagger("openrouter", "http://example.test", "secret", 10, 1).tag(request)
    parts = captured["messages"][1]["content"]
    assert parts[1] == {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,bXA0"}}
    assert not any(p["type"] == "image_url" for p in parts)
    for provider in ["openai", "ollama"]:
        with pytest.raises(ProviderError, match="sampled frames"):
            ChatTagger(provider, "http://example.test", "secret", 10, 1).tag(request)
    with pytest.raises(ProviderError, match="sampled frames"):
        TypeSafeTagger("secret", 10, 1).tag(request)


def test_jev_receives_all_timestamped_frames(settings, image_bytes, monkeypatch):
    captured = {}
    spec = make_spec(settings)

    def fake(url, body, *args):
        captured.update(body)
        return {
            "model": "jev-1.14.0",
            "answers": {
                name: {
                    "choice": "unknown",
                    "confidence": 0.9,
                    "probabilities": {
                        key: 1.0 if key == "unknown" else 0.0 for key in question["criteria"]
                    },
                }
                for name, question in build_questions(spec["questions"]).items()
            },
        }

    monkeypatch.setattr("photo_metadata_agent.inference.providers.typesafe.post_json", fake)
    request = TagRequest(
        None,
        "",
        spec["questions"],
        spec["prompt"],
        "jev-1.14.0",
        frames=((0.5, image_bytes), (2.5, image_bytes)),
    )
    TypeSafeTagger("secret", 10, 1).tag(request)
    assert len(captured["assets"]) == 2
    assert [f["timestamp_seconds"] for f in captured["state"]["frames"]] == [0.5, 2.5]
    assert all(f["photo"] in captured["assets"] for f in captured["state"]["frames"])
