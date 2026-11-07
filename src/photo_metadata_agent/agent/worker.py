import logging
import time

from filelock import FileLock
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from photo_metadata_agent.agent.graph import build_graph
from photo_metadata_agent.inference.contracts import ProviderError
from photo_metadata_agent.settings import Settings
from photo_metadata_agent.storage.repository import Repository

logger = logging.getLogger(__name__)


def process_one(repository: Repository, graph) -> bool:
    job = repository.next_job()
    if job is None:
        return False
    config = {"configurable": {"thread_id": job["id"]}}
    try:
        snapshot = graph.get_state(config)
        # A worker may have crashed after the graph finished but before catalog status changed.
        if snapshot.values and not snapshot.next:
            repository.update(job["id"], status="completed")
            return True
        if job["pending_review"]:
            value = Command(resume=job["pending_review"])
        elif snapshot.values:
            value = None
        else:
            value = {"job_id": job["id"]}
        graph.invoke(value, config)
        status = "needs_review" if graph.get_state(config).next else "completed"
        repository.update(job["id"], status=status)
    except Exception as exc:
        # Do not log raw SDK exceptions, which can contain request bodies and credentials.
        message = (
            str(exc) if isinstance(exc, ProviderError) else f"Job failed ({type(exc).__name__})."
        )
        repository.update(job["id"], status="failed", error=message)
        logger.warning("Job %s: %s", job["id"], message)
    return True


def run_worker(settings: Settings, once: bool = False):
    repository = Repository(settings.database)
    with FileLock(str(settings.data_dir / "worker.lock"), timeout=0):
        repository.recover()
        with SqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.sqlite3")) as saver:
            graph = build_graph(repository, settings, saver)
            while True:
                worked = process_one(repository, graph)
                if once:
                    return
                if not worked:
                    time.sleep(1)
