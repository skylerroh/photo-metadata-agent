"""Video probing, timestamped frame sampling, and bounded H.264 clip preparation.

PyAV uses FFmpeg libraries from its wheels; no shell command or remote URL is accepted.
Tool results contain local paths and provenance rather than bytes in graph checkpoints.
"""

import io
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path

import av
from PIL import Image

from photo_metadata_agent.ingestion.local import atomic_write


class MediaError(ValueError):
    """Safe-to-display decoding or preparation failure."""


@dataclass(frozen=True)
class VideoInfo:
    duration_seconds: float
    width: int
    height: int


def _video_stream(container):
    if not container.streams.video:
        raise MediaError("The file has no video stream.")
    stream = container.streams.video[0]
    if not stream.width or not stream.height or stream.width * stream.height > 40_000_000:
        raise MediaError("Unsupported video dimensions.")
    return stream


def probe_video(path: Path) -> VideoInfo:
    """Inspect a local video with finite duration and decodable frames."""
    if not path.is_file():
        raise MediaError("Video source must be a local file.")
    try:
        with av.open(str(path), options={"protocol_whitelist": "file,pipe"}) as container:
            stream = _video_stream(container)
            duration = (
                float(stream.duration * stream.time_base)
                if stream.duration is not None
                else float(container.duration / av.time_base)
                if container.duration is not None
                else 0
            )
            if not math.isfinite(duration) or not 0 < duration <= 24 * 3600:
                raise MediaError("Video must have a known duration between 0 and 24 hours.")
            next(container.decode(stream))
            return VideoInfo(duration, stream.width, stream.height)
    except MediaError:
        raise
    except (av.FFmpegError, StopIteration, OSError, ValueError) as exc:
        raise MediaError("Video could not be decoded.") from exc


def _read_frame(container, stream, seconds: float):
    # Normalize a stream's nonzero initial PTS to video-relative timestamps.
    start = stream.start_time or 0
    target = start + int(seconds / stream.time_base)
    container.seek(target, stream=stream, backward=True)
    last = None
    for frame in container.decode(stream):
        if frame.pts is None:
            continue
        actual = float((frame.pts - start) * stream.time_base)
        last = (frame, max(0.0, actual))
        if actual + 1e-6 >= seconds:
            return frame, max(0.0, actual)
    if last is not None:
        return last
    raise MediaError("Could not decode a frame at the requested timestamp.")


def _clip_frames(container, stream, start_seconds: float, count: int, fps: int):
    """Decode a clip once, selecting frames for a constant-rate output timeline."""
    start_pts = stream.start_time or 0
    container.seek(start_pts + int(start_seconds / stream.time_base), stream=stream, backward=True)
    decoded = iter(container.decode(stream))
    current, actual = None, -1.0
    for index in range(count):
        target = start_seconds + index / fps
        while actual + 1e-6 < target:
            try:
                frame = next(decoded)
            except StopIteration:
                break
            if frame.pts is not None:
                current = frame
                actual = float((frame.pts - start_pts) * stream.time_base)
        if current is None:
            raise MediaError("The requested video clip contains no frames.")
        yield current


def _image(frame, max_side: int, *, even: bool = False) -> Image.Image:
    image = frame.to_image().convert("RGB")
    # PyAV exposes display-matrix rotation in counter-clockwise degrees.
    rotation = getattr(frame, "rotation", 0)
    if rotation:
        image = image.rotate(rotation, expand=True)
    image.thumbnail((max_side, max_side))
    if even:
        image = image.resize((max(2, image.width // 2 * 2), max(2, image.height // 2 * 2)))
    return image


def _jpeg(frame, max_side: int) -> bytes:
    from PIL import ImageOps

    image = _image(frame, max_side)
    # Match Jev's minimum dimensions without distorting panoramic frames.
    image = ImageOps.pad(image, (max(280, image.width), max(280, image.height)), color="white")
    output = io.BytesIO()
    image.save(output, "JPEG", quality=85)
    return output.getvalue()


def sample_video_frames(
    source: Path,
    output_dir: Path,
    *,
    count: int = 8,
    max_side: int = 768,
) -> dict:
    """Uniformly sample bin centers across the entire video; record actual frame times.

    Sparse sampling cannot establish that an event never happened between frames.
    Repeated timestamps in very short videos are deduplicated.
    """
    if not 1 <= count <= 32 or not 280 <= max_side <= 1568:
        raise MediaError("Use 1–32 frames and a frame side length between 280 and 1568.")
    info = probe_video(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    try:
        with av.open(str(source), options={"protocol_whitelist": "file,pipe"}) as container:
            stream = _video_stream(container)
            # Stay before the final frame's display interval, including one-frame clips.
            rate = float(stream.average_rate or 25)
            end = max(0, info.duration_seconds - 1 / rate)
            for index in range(count):
                target = min(info.duration_seconds * (index + 0.5) / count, end)
                frame, timestamp = _read_frame(container, stream, target)
                if frames and abs(timestamp - frames[-1]["timestamp_seconds"]) < 1e-6:
                    continue
                path = output_dir / f"frame-{index:03d}.jpg"
                atomic_write(path, _jpeg(frame, max_side))
                frames.append({"path": str(path), "timestamp_seconds": timestamp})
    except (av.FFmpegError, OSError, ValueError) as exc:
        raise MediaError("Video frame sampling failed.") from exc
    return {
        "mode": "frames",
        "source": asdict(info),
        "frames": frames,
        "sampling": "uniform-bin-centers-v1",
        "max_side": max_side,
        "audio_included": False,
    }


def downscale_video(
    source: Path,
    output_dir: Path,
    *,
    start_seconds: float = 0,
    max_seconds: float = 30,
    max_side: int = 768,
    fps: int = 4,
    max_bytes: int = 20 * 1024 * 1024,
) -> dict:
    """Create a silent, downscaled MP4 clip for a native video model.

    Temporal resampling caps output FPS, duration and resolution. The original file
    is never modified. Audio is intentionally excluded from this visual tagging task.
    """
    if (
        not math.isfinite(start_seconds)
        or start_seconds < 0
        or not 0 < max_seconds <= 300
        or not 280 <= max_side <= 1568
        or not 1 <= fps <= 30
        or max_bytes <= 0
    ):
        raise MediaError("Invalid video preparation limits.")
    info = probe_video(source)
    if start_seconds >= info.duration_seconds:
        raise MediaError("Video clip start must be before the end of the video.")
    duration = min(max_seconds, info.duration_seconds - start_seconds)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "clip.mp4"
    if source.resolve() == target.resolve():
        raise MediaError("The prepared clip must not overwrite its source.")
    descriptor, temporary_name = tempfile.mkstemp(suffix=".mp4", dir=output_dir)
    os.close(descriptor)
    temporary = Path(temporary_name)
    count = max(1, math.ceil(duration * fps))
    try:
        with (
            av.open(str(source), options={"protocol_whitelist": "file,pipe"}) as container,
            av.open(str(temporary), "w", format="mp4") as out,
        ):
            source_stream = _video_stream(container)
            output_stream = out.add_stream("libx264", rate=fps)
            first, _ = _read_frame(container, source_stream, start_seconds)
            image = _image(first, max_side, even=True)
            output_stream.width, output_stream.height = image.size
            output_stream.pix_fmt = "yuv420p"
            output_stream.options = {"crf": "28", "preset": "veryfast"}
            for index, frame in enumerate(
                _clip_frames(container, source_stream, start_seconds, count, fps)
            ):
                image = _image(frame, max_side, even=True).resize(
                    (output_stream.width, output_stream.height)
                )
                converted = av.VideoFrame.from_image(image)
                converted.pts, converted.time_base = index, Fraction(1, fps)
                for packet in output_stream.encode(converted):
                    out.mux(packet)
                if temporary.stat().st_size > max_bytes:
                    raise MediaError("Prepared video exceeds the configured output size limit.")
            for packet in output_stream.encode():
                out.mux(packet)
        if temporary.stat().st_size > max_bytes:
            raise MediaError("Prepared video exceeds the configured output size limit.")
        temporary.replace(target)
    except MediaError:
        raise
    except (av.FFmpegError, OSError, ValueError) as exc:
        raise MediaError("Video clip preparation failed.") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "mode": "native",
        "source": asdict(info),
        "video_path": str(target),
        "start_seconds": start_seconds,
        "duration_seconds": duration,
        "encoded_duration_seconds": count / fps,
        "fps": fps,
        "max_side": max_side,
        "audio_included": False,
        "format": "video/mp4",
    }


def write_manifest(output_dir: Path, manifest: dict) -> dict:
    atomic_write(output_dir / "manifest.json", json.dumps(manifest, indent=2).encode())
    return manifest
