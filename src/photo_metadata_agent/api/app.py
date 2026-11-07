import hmac
from importlib.resources import files

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from photo_metadata_agent.domain.schemas import ContextTags, Review, Tags
from photo_metadata_agent.metadata.writers import export_sidecar
from photo_metadata_agent.services.catalog import import_image, make_spec
from photo_metadata_agent.services.evaluation import export_jsonl, metrics
from photo_metadata_agent.settings import Settings
from photo_metadata_agent.storage.repository import Repository


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    repository = Repository(settings.database)
    app = FastAPI(title="Photo Metadata Agent", version="0.1.0")
    # This release intentionally serves only localhost. Hosting needs authentication and CSRF.
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
    )
    package = files("photo_metadata_agent")
    ui_path = str(package.joinpath("ui"))
    templates = Jinja2Templates(directory=f"{ui_path}/templates")
    app.mount("/static", StaticFiles(directory=f"{ui_path}/static"), name="static")

    @app.middleware("http")
    async def local_request_guard(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # Block cross-origin forms, including hostile pages targeting a local service.
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site")
            if fetch_site == "cross-site" or (
                origin and not hmac.compare_digest(origin, str(request.base_url).rstrip("/"))
            ):
                return Response("Cross-origin requests are not allowed.", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    def get_job(job_id: str) -> dict:
        try:
            return repository.job(job_id)
        except KeyError as exc:
            raise HTTPException(404, "Job not found") from exc

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, q: str = "", status: str = ""):
        jobs = repository.jobs()
        if status:
            jobs = [job for job in jobs if job["status"] == status]
        if q:
            jobs = [
                job
                for job in jobs
                if q.casefold()
                in (
                    job["filename"]
                    + " "
                    + " ".join(
                        Tags.model_validate(job["effective_tags"]).keywords()
                        if job["effective_tags"]
                        else []
                    )
                ).casefold()
            ]
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "jobs": jobs,
                "settings": settings,
                "q": q,
                "status": status,
            },
        )

    @app.post("/imports")
    async def upload(image: UploadFile = File(...), context: str = Form("")):
        if len(context) > 4000:
            raise HTTPException(422, "Context must be at most 4000 characters.")
        content = await image.read(settings.max_upload_bytes + 1)
        await image.close()
        try:
            asset_id = import_image(
                content, image.filename or "image", "upload", settings, repository
            )
            job_id = repository.enqueue(asset_id, make_spec(settings, context))
        except Exception as exc:
            raise HTTPException(
                422, "Image could not be imported; check size, format and settings."
            ) from exc
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def detail(request: Request, job_id: str):
        job = get_job(job_id)
        return templates.TemplateResponse(
            request=request,
            name="detail.html",
            context={
                "job": job,
                "tags": job["effective_tags"],
                "buckets": ["unknown", "0", "1", "2-3", "4-6", "7-9", "10+"],
                "styles": ["unknown", "candid", "staged", "mixed"],
            },
        )

    @app.get("/jobs/{job_id}/preview")
    def preview(job_id: str):
        job = get_job(job_id)
        return FileResponse(
            settings.data_dir / "previews" / f"{job['asset_id']}.jpg", media_type="image/jpeg"
        )

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = get_job(job_id)
        return {"id": job["id"], "status": job["status"], "error": job["error"]}

    @app.post("/jobs/{job_id}/review")
    def review(
        job_id: str,
        people_count_bucket: str = Form(...),
        capture_style: str = Form(...),
        event: str = Form(...),
        ministry_trip: str = Form(...),
        family: str = Form(...),
        notes: str = Form(""),
    ):
        get_job(job_id)
        options = {"yes": True, "no": False, "unknown": None}
        try:
            annotation = Review(
                tags=Tags(
                    people_count_bucket=people_count_bucket,
                    capture_style=capture_style,
                    contexts=ContextTags(
                        event=options[event],
                        ministry_trip=options[ministry_trip],
                        family=options[family],
                    ),
                ),
                notes=notes,
            )
            repository.submit_review(job_id, annotation)
        except (ValueError, KeyError, ValidationError) as exc:
            raise HTTPException(422, "Invalid review or job is no longer awaiting review.") from exc
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/rerun")
    def rerun(job_id: str):
        job = get_job(job_id)
        new_id = repository.enqueue(job["asset_id"], make_spec(settings, job["spec"]["context"]))
        return RedirectResponse(f"/jobs/{new_id}", status_code=303)

    @app.get("/jobs/{job_id}/metadata.xmp")
    def export(job_id: str):
        try:
            path = export_sidecar(get_job(job_id), settings.data_dir)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return FileResponse(path, media_type="application/rdf+xml", filename=path.name)

    @app.get("/evals.jsonl")
    def export_evals():
        return Response(
            export_jsonl(repository),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": 'attachment; filename="annotations.jsonl"',
            },
        )

    @app.get("/api/evals/metrics")
    def eval_metrics():
        return metrics(repository)

    return app
