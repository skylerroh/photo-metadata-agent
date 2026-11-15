from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from photo_metadata_agent.inference.factory import create_tagger
from photo_metadata_agent.services.media import prepare_media, tag_request
from photo_metadata_agent.settings import Settings
from photo_metadata_agent.storage.repository import Repository


class TagState(TypedDict, total=False):
    job_id: str
    prediction: dict
    prepared: dict


def build_graph(repository: Repository, settings: Settings, checkpointer):
    def prepare(state: TagState):
        job = repository.job(state["job_id"])
        if job["prediction"]:
            return {}
        return {"prepared": prepare_media(job, settings.data_dir)}

    def infer(state: TagState):
        job = repository.job(state["job_id"])
        # A crash after catalog persistence should not make another paid request.
        if job["prediction"]:
            return {"prediction": job["prediction"]}
        spec = job["spec"]
        prepared = state.get("prepared") or prepare_media(job, settings.data_dir)
        started = perf_counter()
        prediction = create_tagger(spec).tag(tag_request(job, prepared)).model_dump()
        prediction["preparation"] = prepared
        prediction["latency_ms"] = (perf_counter() - started) * 1000
        repository.update(job["id"], status="running", prediction=prediction)
        return {"prediction": prediction}

    def review(state: TagState):
        payload = interrupt({"job_id": state["job_id"], "prediction": state["prediction"]})
        repository.save_review(state["job_id"], payload)
        return {}

    graph = StateGraph(TagState)
    graph.add_node("prepare", prepare)
    graph.add_node("infer", infer)
    graph.add_node("review", review)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "infer")
    graph.add_edge("infer", "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)
