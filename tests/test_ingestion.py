import io

import pytest
from PIL import Image, UnidentifiedImageError

from photo_metadata_agent.ingestion.local import prepare_image
from photo_metadata_agent.services.catalog import import_image


def test_normalizes_orientation_and_strips_exif():
    image = Image.new("RGB", (400, 800), "red")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "Private metadata"
    stream = io.BytesIO()
    image.save(stream, "JPEG", exif=exif)
    with Image.open(io.BytesIO(prepare_image(stream.getvalue()))) as prepared:
        assert prepared.size == (800, 400)
        assert not prepared.getexif()


def test_panorama_is_padded_to_provider_minimum():
    stream = io.BytesIO()
    Image.new("RGB", (3000, 100)).save(stream, "PNG")
    with Image.open(io.BytesIO(prepare_image(stream.getvalue()))) as prepared:
        assert prepared.size == (1568, 280)


def test_duplicates_share_asset_and_preserve_source_refs(settings, repository, image_bytes):
    first = import_image(image_bytes, "a.jpg", "album-a", settings, repository)
    second = import_image(image_bytes, "b.jpg", "album-b", settings, repository)
    assert first == second
    with repository.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 2


def test_invalid_image_creates_no_asset(settings, repository):
    with pytest.raises(UnidentifiedImageError):
        import_image(b"not an image", "bad.jpg", "test", settings, repository)
    with repository.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0


def test_oversized_image_rejected(settings, repository, image_bytes):
    settings.max_upload_bytes = 10
    with pytest.raises(ValueError, match="limit"):
        import_image(image_bytes, "sample.jpg", "test", settings, repository)
