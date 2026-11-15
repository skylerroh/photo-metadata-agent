import json
from pathlib import Path

import av
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from photo_metadata_agent.agent.worker import run_worker
from photo_metadata_agent.api.app import create_app
from photo_metadata_agent.domain.schemas import Review
from photo_metadata_agent.inference.contracts import ProviderError
from photo_metadata_agent.services.catalog import import_media, make_spec
from photo_metadata_agent.services.media import prepare_media, tag_request, video_mode
from photo_metadata_agent.tools.video import MediaError, downscale_video, sample_video_frames


def test_frames_cover_timeline_preserve_colors_and_downscale(video_path, tmp_path):
    result = sample_video_frames(video_path, tmp_path / "frames", count=4, max_side=320)
    timestamps = [item["timestamp_seconds"] for item in result["frames"]]
    assert len(timestamps) == 4
    assert timestamps == sorted(timestamps)
    assert timestamps[0] < 0.5 and timestamps[-1] > 1.5
    with Image.open(result["frames"][0]["path"]) as first:
        assert first.size == (320, 280)
        assert first.getpixel((160, 140))[0] > 200
    with Image.open(result["frames"][-1]["path"]) as last:
        assert last.getpixel((160, 140))[2] > 200
    assert result["audio_included"] is False


def test_native_clip_limits_and_source_preservation(video_path, tmp_path):
    original = video_path.read_bytes()
    result = downscale_video(
        video_path, tmp_path / "clip", start_seconds=1, max_seconds=0.5, max_side=320, fps=4
    )
    assert result["duration_seconds"] == 0.5
    with av.open(result["video_path"]) as prepared:
        assert len(prepared.streams.audio) == 0
        assert prepared.streams.video[0].width == 320
        assert prepared.streams.video[0].height == 180
        assert float(prepared.streams.video[0].average_rate) == 4
        frames = list(prepared.decode(video=0))
        assert len(frames) == 2
        assert frames[0].to_image().getpixel((100, 100))[2] > 200
    assert video_path.read_bytes() == original


def test_invalid_video_and_limits_fail_cleanly(tmp_path, video_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not video")
    with pytest.raises(MediaError):
        sample_video_frames(bad, tmp_path / "bad-frames")
    with pytest.raises(MediaError):
        sample_video_frames(video_path, tmp_path / "frames", count=0)
    with pytest.raises(MediaError):
        downscale_video(video_path, tmp_path / "clip", start_seconds=5)
    with pytest.raises(MediaError, match="size limit"):
        downscale_video(video_path, tmp_path / "oversized", max_bytes=1)
    assert not (tmp_path / "oversized" / "clip.partial.mp4").exists()
    assert not (tmp_path / "oversized" / "clip.mp4").exists()


@pytest.mark.parametrize(
    "provider,capability,expected",
    [
        ("openrouter", True, "native"),
        ("openrouter", False, "frames"),
        ("typesafe", True, "frames"),
        ("ollama", False, "frames"),
        ("openai", False, "frames"),
    ],
)
def test_auto_routes_by_adapter_and_declared_model_capability(provider, capability, expected):
    spec = {"provider": provider, "model_supports_video": capability, "video_mode": "auto"}
    assert video_mode(spec) == expected
    spec["video_mode"] = "frames"
    assert video_mode(spec) == "frames"
    if expected == "frames":
        spec["video_mode"] = "native"
        with pytest.raises(ProviderError, match="Native video requires"):
            video_mode(spec)


def test_video_import_worker_restart_review_and_ui(
    settings,
    repository,
    video_path,
    review_body,
):
    content = video_path.read_bytes()
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/imports", files={"image": ("clip.mp4", content, "video/mp4")}, follow_redirects=False
        )
        assert response.status_code == 303
        location = response.headers["location"]
        job_id = location.split("/")[-1]
        run_worker(settings, once=True)
        job = repository.job(job_id)
        assert job["media_type"] == "video"
        assert job["status"] == "needs_review"
        assert job["prediction"]["preparation"]["mode"] == "frames"
        assert (settings.data_dir / "originals" / job["asset_id"]).read_bytes() == content
        assert "<video " in client.get(location).text
        assert "8 frames sampled" in client.get(location).text
        video = client.get(location + "/video")
        assert video.content == content
        assert video.headers["content-type"] == "video/mp4"
        repository.submit_review(job_id, Review.model_validate(review_body))
        run_worker(settings, once=True)
        assert repository.job(job_id)["status"] == "completed"
        assert client.get(location + "/metadata.xmp").status_code == 200
        record = json.loads(client.get("/evals.jsonl").text)
        assert record["prediction"]["preparation"]["mode"] == "frames"


def test_native_graph_sends_only_prepared_clip(settings, repository, video_path, monkeypatch):
    settings.provider, settings.model = "openrouter", "video-model"
    settings.model_supports_video = True
    asset = import_media(video_path.read_bytes(), "clip.mp4", "test", settings, repository)
    job_id = repository.enqueue(asset, make_spec(settings))
    from photo_metadata_agent.inference.providers.demo import DemoTagger

    class CaptureTagger:
        def tag(self, request):
            assert request.video is not None
            assert not request.images()
            assert request.media_context["mode"] == "native"
            assert "video_path" not in request.media_context
            assert "maximum number" in request.questions["people_count_bucket"]["instructions"]
            return DemoTagger().tag(request)

    monkeypatch.setattr("photo_metadata_agent.agent.graph.create_tagger", lambda _: CaptureTagger())
    run_worker(settings, once=True)
    job = repository.job(job_id)
    assert job["status"] == "needs_review"
    assert Path(job["prediction"]["preparation"]["video_path"]).exists()


def test_frame_request_keeps_timestamps_and_no_source_paths(settings, repository, video_path):
    asset = import_media(video_path.read_bytes(), "clip.mp4", "test", settings, repository)
    job = repository.job(repository.enqueue(asset, make_spec(settings)))
    prepared = prepare_media(job, settings.data_dir)
    request = tag_request(job, prepared)
    assert len(request.frames) == 8
    assert request.frames[0][0] < request.frames[-1][0]
    assert str(settings.data_dir) not in json.dumps(request.media_context)


def test_preparation_cannot_overwrite_source(video_path, tmp_path):
    target = tmp_path / "clip.mp4"
    target.write_bytes(video_path.read_bytes())
    original = target.read_bytes()
    with pytest.raises(MediaError, match="overwrite"):
        downscale_video(target, tmp_path)
    assert target.read_bytes() == original


def test_upload_rejects_unsupported_native_before_import(settings, video_path):
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/imports",
            files={"image": ("clip.mp4", video_path.read_bytes())},
            data={"video_mode": "native"},
        )
        assert response.status_code == 422
        assert "Native video requires" in response.text


def test_migration_preserves_existing_image_catalog(tmp_path):
    import sqlite3

    from photo_metadata_agent.storage.repository import DDL, Repository

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(DDL)
        db.execute("INSERT INTO assets(id,filename) VALUES ('old','old.jpg')")
        db.execute("PRAGMA user_version=1")
    repository = Repository(path)
    Repository(path)  # Migration is safe to repeat.
    with repository.connect() as db:
        row = db.execute("SELECT media_type FROM assets WHERE id='old'").fetchone()
        assert row[0] == "image"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_single_frame_video_deduplicates_samples(tmp_path):
    source = tmp_path / "single.mp4"
    with av.open(str(source), "w") as output:
        stream = output.add_stream("libx264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 320, 280, "yuv420p"
        for packet in stream.encode(av.VideoFrame.from_image(Image.new("RGB", (320, 280)))):
            output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    sampled = sample_video_frames(source, tmp_path / "single-frames", count=8)
    assert len(sampled["frames"]) == 1
    clip = downscale_video(source, tmp_path / "single-clip", fps=4)
    assert clip["duration_seconds"] == pytest.approx(0.1)
    assert clip["encoded_duration_seconds"] == 0.25


def test_clip_can_start_during_last_frames_display_interval(video_path, tmp_path):
    result = downscale_video(video_path, tmp_path / "tail", start_seconds=1.99, fps=4)
    assert result["duration_seconds"] == pytest.approx(0.01)
    with av.open(result["video_path"]) as video:
        assert len(list(video.decode(video=0))) == 1
