import json
from xml.etree import ElementTree as ET

import pytest

from photo_metadata_agent.agent.worker import run_worker
from photo_metadata_agent.domain.schemas import Review
from photo_metadata_agent.metadata.writers import export_sidecar
from photo_metadata_agent.services.evaluation import export_jsonl, metrics


def test_review_survives_worker_restart_and_preserves_prediction(
    settings,
    repository,
    job_id,
    review_body,
    image_bytes,
):
    run_worker(settings, once=True)
    predicted = repository.job(job_id)
    assert predicted["status"] == "needs_review"
    assert predicted["prediction"]["tags"]["people_count_bucket"] == "unknown"
    with pytest.raises(ValueError, match="Review"):
        export_sidecar(predicted, settings.data_dir)
    repository.submit_review(job_id, Review.model_validate(review_body))
    # A new worker opens durable checkpoints and resumes the existing graph.
    run_worker(settings, once=True)
    reviewed = repository.job(job_id)
    assert reviewed["status"] == "completed"
    assert reviewed["pending_review"] is None
    assert reviewed["prediction"] == predicted["prediction"]
    assert reviewed["effective_tags"] == review_body["tags"]
    assert len(reviewed["reviews"]) == 1
    first = export_sidecar(reviewed, settings.data_dir)
    assert first == export_sidecar(reviewed, settings.data_dir)
    root = ET.fromstring(first.read_bytes())
    keywords = [node.text for node in root.iter("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li")]
    assert set(keywords) == {"people:4-6", "capture:candid", "context:event", "context:family"}
    assert (settings.data_dir / "originals" / reviewed["asset_id"]).read_bytes() == image_bytes
    record = json.loads(export_jsonl(repository))
    assert record["annotation"]["tags"] == review_body["tags"]
    assert record["spec"]["prompt_sha256"]
    assert metrics(repository)["fields"]["people_count_bucket"] == {"n": 1, "accuracy": 0.0}


def test_duplicate_review_submission_is_rejected(settings, repository, job_id, review_body):
    run_worker(settings, once=True)
    review = Review.model_validate(review_body)
    repository.submit_review(job_id, review)
    with pytest.raises(ValueError, match="awaiting review"):
        repository.submit_review(job_id, review)
    run_worker(settings, once=True)
    assert len(repository.job(job_id)["reviews"]) == 1


def test_recovery_reuses_saved_prediction(settings, repository, job_id, monkeypatch):
    run_worker(settings, once=True)
    prediction = repository.job(job_id)["prediction"]
    repository.update(job_id, status="running")  # Simulate a crash before status persistence.

    def no_inference(*args):
        raise AssertionError("Inference must not run twice")

    monkeypatch.setattr("photo_metadata_agent.agent.graph.create_tagger", no_inference)
    run_worker(settings, once=True)
    assert repository.job(job_id)["status"] == "needs_review"
    assert repository.job(job_id)["prediction"] == prediction


def test_unknown_human_labels_excluded_from_accuracy(settings, repository, job_id, review_body):
    run_worker(settings, once=True)
    review_body["tags"]["contexts"]["family"] = None
    repository.submit_review(job_id, Review.model_validate(review_body))
    run_worker(settings, once=True)
    assert "contexts.family" not in metrics(repository)["fields"]


def test_provider_failure_does_not_poison_queue(settings, repository, job_id, monkeypatch):
    from photo_metadata_agent.inference.contracts import ProviderError

    def fail(*args):
        raise ProviderError("Provider returned HTTP 401.")

    monkeypatch.setattr("photo_metadata_agent.agent.graph.create_tagger", fail)
    run_worker(settings, once=True)
    job = repository.job(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "Provider returned HTTP 401."
    assert job["prediction"] is None


def test_crash_during_review_resume_is_idempotent(
    settings,
    repository,
    job_id,
    review_body,
    monkeypatch,
):
    run_worker(settings, once=True)
    repository.submit_review(job_id, Review.model_validate(review_body))
    original_save = type(repository).save_review

    def crash_after_save(self, job_id, payload):
        original_save(self, job_id, payload)
        raise SystemExit("Simulated process termination")

    monkeypatch.setattr(type(repository), "save_review", crash_after_save)
    with pytest.raises(SystemExit):
        run_worker(settings, once=True)
    assert repository.job(job_id)["status"] == "running"
    assert repository.job(job_id)["pending_review"] is not None
    monkeypatch.setattr(type(repository), "save_review", original_save)
    run_worker(settings, once=True)
    assert repository.job(job_id)["status"] == "completed"
    assert len(repository.job(job_id)["reviews"]) == 1
