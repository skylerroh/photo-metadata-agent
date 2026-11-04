from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from photo_metadata_agent.inference.contracts import TagRequest
from photo_metadata_agent.inference.factory import create_tagger
from photo_metadata_agent.settings import Settings
from photo_metadata_agent.storage.repository import Repository


class TagState(TypedDict, total=False):
    job_id: str
    prediction: dict


def build_graph(repository: Repository, settings: Settings, checkpointer):
    def infer(state: TagState):
        job = repository.job(state["job_id"])
        # A crash after catalog persistence should not make another paid request.
        if job["prediction"]:
            return {"prediction": job["prediction"]}
        spec = job["spec"]
        image = (settings.data_dir / "previews" / f"{job['asset_id']}.jpg").read_bytes()
        started = perf_counter()
        prediction = (
            create_tagger(spec)
            .tag(
                TagRequest(
                    image=image,
                    context=spec["context"],
                    questions=spec["questions"],
                    prompt=spec["prompt"],
                    model=spec["model"],
                )
            )
            .model_dump()
        )
        prediction["latency_ms"] = (perf_counter() - started) * 1000
        repository.update(job["id"], status="running", prediction=prediction)
        return {"prediction": prediction}

    def review(state: TagState):
        payload = interrupt({"job_id": state["job_id"], "prediction": state["prediction"]})
        repository.save_review(state["job_id"], payload)
        return {}

    graph = StateGraph(TagState)
    graph.add_node("infer", infer)
    graph.add_node("review", review)
    graph.add_edge(START, "infer")
    graph.add_edge("infer", "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)
