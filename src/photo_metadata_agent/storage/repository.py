"""SQLite catalog, queue, immutable predictions and append-only reviews."""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from photo_metadata_agent.domain.schemas import Review

DDL = """
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, filename TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sources (
 asset_id TEXT NOT NULL REFERENCES assets(id), source TEXT NOT NULL,
 PRIMARY KEY(asset_id, source)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
 status TEXT NOT NULL DEFAULT 'queued', spec TEXT NOT NULL,
 prediction TEXT, error TEXT, pending_review TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), body TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
PRAGMA user_version=1;
"""


class Repository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(DDL)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            yield db

    def add_asset(self, asset_id: str, filename: str, source: str):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO assets(id,filename) VALUES (?,?)", (asset_id, filename)
            )
            db.execute("INSERT OR IGNORE INTO sources VALUES (?,?)", (asset_id, source))

    def enqueue(self, asset_id: str, spec: dict) -> str:
        job_id = str(uuid4())
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs(id,asset_id,spec) VALUES (?,?,?)",
                (job_id, asset_id, json.dumps(spec)),
            )
        return job_id

    def job(self, job_id: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT j.*, a.filename FROM jobs j JOIN assets a ON a.id=j.asset_id WHERE j.id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            result = dict(row)
            for field in ("spec", "prediction", "pending_review"):
                result[field] = json.loads(result[field]) if result[field] else None
            reviews = db.execute(
                "SELECT body FROM reviews WHERE job_id=? ORDER BY rowid", (job_id,)
            ).fetchall()
            result["reviews"] = [json.loads(r["body"]) for r in reviews]
            result["effective_tags"] = (
                result["reviews"][-1]["tags"]
                if reviews
                else result["prediction"]["tags"]
                if result["prediction"]
                else None
            )
            return result

    def jobs(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            ids = db.execute("SELECT id FROM jobs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [self.job(row["id"]) for row in ids]

    def update(self, job_id: str, *, status: str, prediction=None, error=None):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?, prediction=COALESCE(?,prediction), error=?, "
                "pending_review=CASE WHEN ?='completed' THEN NULL ELSE pending_review END, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, json.dumps(prediction) if prediction else None, error, status, job_id),
            )

    def next_job(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM jobs WHERE status IN ('queued','queued_review') "
                "ORDER BY rowid LIMIT 1"
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (row["id"],))
        return self.job(row["id"])

    def recover(self):
        # Called only after obtaining the exclusive worker lock.
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=CASE WHEN pending_review IS NULL THEN 'queued' "
                "ELSE 'queued_review' END WHERE status='running'"
            )

    def submit_review(self, job_id: str, review: Review):
        payload = {"id": str(uuid4()), "body": review.model_dump()}
        with self.connect() as db:
            result = db.execute(
                "UPDATE jobs SET pending_review=?, status='queued_review' "
                "WHERE id=? AND status='needs_review'",
                (json.dumps(payload), job_id),
            )
            if result.rowcount != 1:
                raise ValueError("This job is not awaiting review. Refresh before submitting.")

    def save_review(self, job_id: str, payload: dict):
        review = Review.model_validate(payload["body"])
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO reviews(id,job_id,body) VALUES (?,?,?)",
                (payload["id"], job_id, review.model_dump_json()),
            )
