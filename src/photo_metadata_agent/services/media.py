"""Select the supported video path before preparing or sending media."""

from pathlib import Path

from photo_metadata_agent.inference.contracts import ProviderError, TagRequest
from photo_metadata_agent.tools.video import downscale_video, sample_video_frames, write_manifest

VIDEO_POLICY = (
    "This input is a video sample, not a single photo. Label only the footage provided. "
    "For people count, use the maximum number simultaneously visible in any supplied frame; "
    "do not sum people across time or assume unique identities. Capture style is mixed if "
    "posed and candid scenes both occur. Sparse frames or a trimmed clip may miss events; "
    "do not infer an absence in the full video from absence in the sample. Audio is excluded. "
)


def video_mode(spec: dict) -> str:
    requested = spec.get("video_mode", "auto")
    supported = spec["provider"] == "openrouter" and spec.get("model_supports_video", False)
    if requested == "native" and not supported:
        raise ProviderError(
            "Native video requires the OpenRouter adapter and MODEL_SUPPORTS_VIDEO=true "
            "for a compatible model; use frames otherwise."
        )
    return "native" if requested != "frames" and supported else "frames"


def prepare_media(job: dict, data_dir: Path) -> dict:
    if job.get("media_type", "image") == "image":
        return {
            "mode": "image",
            "image_path": str(data_dir / "previews" / f"{job['asset_id']}.jpg"),
        }
    spec = job["spec"]
    mode = video_mode(spec)
    source = data_dir / "originals" / job["asset_id"]
    directory = data_dir / "prepared" / job["id"]
    if mode == "native":
        result = downscale_video(
            source,
            directory,
            start_seconds=spec.get("video_start_seconds", 0),
            max_seconds=spec.get("video_max_seconds", 30),
            max_side=spec.get("video_max_side", 768),
            fps=spec.get("video_fps", 4),
            max_bytes=spec.get("video_max_bytes", 20 * 1024 * 1024),
        )
    else:
        result = sample_video_frames(
            source,
            directory,
            count=spec.get("video_frame_count", 8),
            max_side=spec.get("video_max_side", 768),
        )
    result["policy_version"] = "video-tags-v1"
    return write_manifest(directory, result)


def tag_request(job: dict, prepared: dict) -> TagRequest:
    spec = job["spec"]
    is_video = prepared["mode"] != "image"
    questions = dict(spec["questions"])
    if is_video:
        questions["people_count_bucket"] = {
            **questions["people_count_bucket"],
            "instructions": "What is the maximum number of real people simultaneously visible "
            "in any provided video frame? Do not sum across frames. Choose unknown when unclear.",
        }
        questions["capture_style"] = {
            **questions["capture_style"],
            "instructions": "Is the provided video footage candid, deliberately staged, "
            "mixed across scenes, or unknown?",
        }
    # Retain timestamps and coverage, but avoid sending local filesystem paths to providers.
    context = {k: v for k, v in prepared.items() if k not in ("video_path", "image_path", "frames")}
    if "frames" in prepared:
        context["frame_timestamps_seconds"] = [f["timestamp_seconds"] for f in prepared["frames"]]
    return TagRequest(
        image=Path(prepared["image_path"]).read_bytes() if not is_video else None,
        context=spec["context"],
        questions=questions,
        prompt=(VIDEO_POLICY if is_video else "") + spec["prompt"],
        model=spec["model"],
        frames=tuple(
            (f["timestamp_seconds"], Path(f["path"]).read_bytes())
            for f in prepared.get("frames", [])
        ),
        video=Path(prepared["video_path"]).read_bytes() if "video_path" in prepared else None,
        media_context=context if is_video else None,
    )
