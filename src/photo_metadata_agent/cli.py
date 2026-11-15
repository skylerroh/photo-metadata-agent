import argparse
import json
import logging
from pathlib import Path

from photo_metadata_agent.agent.worker import run_worker
from photo_metadata_agent.domain.schemas import Tags
from photo_metadata_agent.ingestion.local import SUPPORTED
from photo_metadata_agent.ingestion.video import VIDEO_EXTENSIONS
from photo_metadata_agent.metadata.writers import export_sidecar
from photo_metadata_agent.services.catalog import import_media, make_spec
from photo_metadata_agent.services.evaluation import export_jsonl, metrics
from photo_metadata_agent.services.media import video_mode
from photo_metadata_agent.settings import Settings
from photo_metadata_agent.storage.repository import Repository
from photo_metadata_agent.tools.video import downscale_video, sample_video_frames, write_manifest


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Personal photo tagging and review")
    root.add_argument("--data-dir", type=Path, help="Catalog and managed image directory")
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local review UI")
    serve.add_argument("--port", type=int, default=8000)
    worker = commands.add_parser("worker", help="Run the durable tagging worker")
    worker.add_argument("--once", action="store_true", help="Process at most one queued job")
    ingest = commands.add_parser("import", help="Import a file or recursively scan a directory")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--context", default="", help="Album or occasion context")
    ingest.add_argument("--questions", type=Path, help="Custom question definitions JSON")
    ingest.add_argument(
        "--provider", choices=["demo", "typesafe", "openai", "openrouter", "ollama"]
    )
    ingest.add_argument("--model")
    ingest.add_argument("--video-mode", choices=["auto", "native", "frames"])
    ingest.add_argument("--model-supports-video", action="store_true", default=None)
    prepare = commands.add_parser("prepare-video", help="Prepare a downscaled clip or JPEG samples")
    prepare.add_argument("path", type=Path)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--mode", choices=["native", "frames"], default="frames")
    prepare.add_argument("--frames", type=int, default=8)
    prepare.add_argument("--max-side", type=int, default=768)
    prepare.add_argument("--start", type=float, default=0)
    prepare.add_argument("--seconds", type=float, default=30)
    prepare.add_argument("--fps", type=int, default=4)
    export = commands.add_parser("export", help="Export reviewed tags as XMP")
    export.add_argument("job_id")
    evaluation = commands.add_parser("evals", help="Export reviewed runs and baseline metrics")
    evaluation.add_argument("--output", type=Path)
    schema = commands.add_parser("schema", help="Generate the canonical tagging JSON schema")
    schema.add_argument("--output", type=Path, default=Path("configs/schemas/tags.schema.json"))
    return root


def main():
    args = parser().parse_args()
    settings = Settings(**({"data_dir": args.data_dir} if args.data_dir else {}))
    logging.basicConfig(level=logging.INFO)
    if args.command == "serve":
        import uvicorn

        from photo_metadata_agent.api.app import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)
    elif args.command == "worker":
        run_worker(settings, once=args.once)
    elif args.command == "prepare-video":
        if args.mode == "frames":
            result = sample_video_frames(
                args.path, args.output, count=args.frames, max_side=args.max_side
            )
        else:
            result = downscale_video(
                args.path,
                args.output,
                start_seconds=args.start,
                max_seconds=args.seconds,
                max_side=args.max_side,
                fps=args.fps,
            )
        write_manifest(args.output, result)
        print(json.dumps(result, indent=2))
    elif args.command == "schema":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(Tags.model_json_schema(), indent=2) + "\n")
        print(args.output)
    else:
        repository = Repository(settings.database)
        if args.command == "import":
            if args.provider:
                settings.provider = args.provider
            if args.model:
                settings.model = args.model
            if args.video_mode:
                settings.video_mode = args.video_mode
            if args.model_supports_video is not None:
                settings.model_supports_video = args.model_supports_video
            if not args.path.exists():
                raise SystemExit(f"Path does not exist: {args.path}")
            if len(args.context) > 4000:
                raise SystemExit("Context must be at most 4000 characters.")
            spec = make_spec(settings, args.context, args.questions)
            paths = [args.path] if args.path.is_file() else sorted(args.path.rglob("*"))
            imported, failed = 0, 0
            for path in paths:
                if not path.is_file() or path.suffix.lower() not in SUPPORTED | VIDEO_EXTENSIONS:
                    continue
                try:
                    is_video = path.suffix.lower() in VIDEO_EXTENSIONS
                    limit = (
                        settings.max_video_upload_bytes if is_video else settings.max_upload_bytes
                    )
                    if path.stat().st_size > limit:
                        raise ValueError("Media exceeds the upload limit")
                    if is_video:
                        video_mode(spec)
                    asset_id = import_media(
                        path.read_bytes(), path.name, str(path.resolve()), settings, repository
                    )
                    job_id = repository.enqueue(asset_id, spec)
                    print(f"Queued {path.name}: {job_id}")
                    imported += 1
                except Exception as exc:
                    print(f"Skipped {path.name}: {type(exc).__name__}")
                    failed += 1
            print(f"Imported {imported}; failed {failed}.")
            if failed or not imported:
                raise SystemExit(1)
        elif args.command == "export":
            print(export_sidecar(repository.job(args.job_id), settings.data_dir))
        elif args.command == "evals":
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(export_jsonl(repository))
            print(json.dumps(metrics(repository), indent=2))


if __name__ == "__main__":
    main()
