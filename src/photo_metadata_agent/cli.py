import argparse
import json
import logging
from pathlib import Path

from photo_metadata_agent.agent.worker import run_worker
from photo_metadata_agent.domain.schemas import Tags
from photo_metadata_agent.ingestion.local import SUPPORTED
from photo_metadata_agent.metadata.writers import export_sidecar
from photo_metadata_agent.services.catalog import import_image, make_spec
from photo_metadata_agent.services.evaluation import export_jsonl, metrics
from photo_metadata_agent.settings import Settings
from photo_metadata_agent.storage.repository import Repository


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
            if not args.path.exists():
                raise SystemExit(f"Path does not exist: {args.path}")
            if len(args.context) > 4000:
                raise SystemExit("Context must be at most 4000 characters.")
            spec = make_spec(settings, args.context, args.questions)
            paths = [args.path] if args.path.is_file() else sorted(args.path.rglob("*"))
            imported, failed = 0, 0
            for path in paths:
                if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                    continue
                try:
                    if path.stat().st_size > settings.max_upload_bytes:
                        raise ValueError("Image exceeds the upload limit")
                    asset_id = import_image(
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
