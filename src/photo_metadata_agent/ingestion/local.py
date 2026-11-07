"""Copy originals into managed storage; inference receives a normalized derivative."""

import hashlib
import io
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".heif"}


def prepare_image(content: bytes) -> bytes:
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    with Image.open(io.BytesIO(content)) as source:
        if getattr(source, "n_frames", 1) != 1:
            raise ValueError("Only still images are supported.")
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1568, 1568))
        # Jev requires both dimensions >= 280. Pad rather than distort panoramas.
        image = ImageOps.pad(image, (max(280, image.width), max(280, image.height)), color="white")
        output = io.BytesIO()
        image.save(output, "JPEG", quality=90)
        return output.getvalue()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ingest(content: bytes, filename: str, data_dir: Path) -> str:
    asset_id = hashlib.sha256(content).hexdigest()
    derivative = prepare_image(content)  # Validate before writing anything.
    atomic_write(data_dir / "originals" / asset_id, content)
    atomic_write(data_dir / "previews" / f"{asset_id}.jpg", derivative)
    return asset_id
