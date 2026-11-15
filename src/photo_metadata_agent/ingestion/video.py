import hashlib
import tempfile
from pathlib import Path

import av

from photo_metadata_agent.ingestion.local import atomic_write
from photo_metadata_agent.tools.video import _jpeg, _read_frame, _video_stream, probe_video

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


def ingest_video(content: bytes, data_dir: Path) -> str:
    """Validate the video and preserve original bytes, with a JPEG poster for the library."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data_dir) as temporary:
        source = Path(temporary) / "source"
        source.write_bytes(content)
        probe_video(source)
        with av.open(str(source)) as container:
            frame, _ = _read_frame(container, _video_stream(container), 0)
            poster = _jpeg(frame, 768)
    asset_id = hashlib.sha256(content).hexdigest()
    atomic_write(data_dir / "originals" / asset_id, content)
    atomic_write(data_dir / "previews" / f"{asset_id}.jpg", poster)
    return asset_id
