"""Export reviewed prediction/label pairs and descriptive baseline metrics."""

import json

from photo_metadata_agent.storage.repository import Repository


def records(repository: Repository) -> list[dict]:
    with repository.connect() as db:
        ids = [row[0] for row in db.execute("SELECT DISTINCT job_id FROM reviews ORDER BY job_id")]
    result = []
    for job_id in ids:
        job = repository.job(job_id)
        result.append(
            {
                "asset_id": job["asset_id"],
                "job_id": job_id,
                "group": job["spec"]["context"],
                "spec": job["spec"],
                "prediction": job["prediction"],
                "annotation": job["reviews"][-1],
            }
        )
    return result


def export_jsonl(repository: Repository) -> str:
    return "".join(json.dumps(record) + "\n" for record in records(repository))


def metrics(repository: Repository) -> dict:
    rows = records(repository)
    comparisons: dict[str, list[bool]] = {}
    abstentions = 0
    for row in rows:
        predicted = row["prediction"]["tags"]
        actual = row["annotation"]["tags"]
        pairs = [
            (field, predicted[field], actual[field])
            for field in ("people_count_bucket", "capture_style")
        ]
        pairs.extend(
            (f"contexts.{field}", predicted["contexts"][field], value)
            for field, value in actual["contexts"].items()
        )
        for field, prediction, label in pairs:
            abstentions += prediction is None or prediction == "unknown"
            if label is None or label == "unknown":
                continue  # Unknown human labels are not ground truth negatives.
            comparisons.setdefault(field, []).append(prediction == label)
    return {
        "reviewed_runs": len(rows),
        "prediction_abstention_rate": abstentions / (len(rows) * 5) if rows else None,
        "fields": {
            field: {"n": len(values), "accuracy": sum(values) / len(values)}
            for field, values in comparisons.items()
        },
        "note": (
            "Descriptive results on reviewed runs; not a held-out benchmark. Demo runs included."
        ),
    }
