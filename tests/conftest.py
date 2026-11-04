import io

import pytest
from PIL import Image

from photo_metadata_agent.services.catalog import import_image, make_spec
from photo_metadata_agent.settings import Settings
from photo_metadata_agent.storage.repository import Repository


@pytest.fixture
def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (600, 400), "#bad5c6").save(output, "JPEG")
    return output.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, provider="demo", model="demo-v1", _env_file=None)


@pytest.fixture
def repository(settings):
    return Repository(settings.database)


@pytest.fixture
def job_id(settings, repository, image_bytes):
    asset_id = import_image(image_bytes, "sample.jpg", "test", settings, repository)
    return repository.enqueue(asset_id, make_spec(settings, "Family picnic"))


@pytest.fixture
def review_body():
    return {
        "tags": {
            "people_count_bucket": "4-6",
            "capture_style": "candid",
            "contexts": {"event": True, "ministry_trip": False, "family": True},
        },
        "notes": "Corrected after visual inspection.",
    }
