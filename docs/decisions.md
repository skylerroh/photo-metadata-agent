# Design decisions

## 001: Strict shared annotation contract

Use Pydantic contracts and generated JSON Schema. Share allowed values across review,
provider output, storage, and metadata mapping. Keep category descriptions versioned and
snapshotted. Changes to allowed values require an explicit schema version change.

## 002: Direct provider adapters behind a small interface

Use a `Tagger` protocol and explicit HTTP payload adapters. OpenAI and OpenRouter use
Chat Completions; Ollama uses its native chat API; Jev uses System One typed questions.
This avoids making a chat-only abstraction dictate Jev's request semantics. All calls
return validated `Prediction` objects. Provider capabilities remain a configuration
responsibility; v1 has no automatic model discovery or fallback.

## 003: Durable bounded LangGraph workflow

The graph infers, persists, interrupts for review, and resumes. Application code controls
all side effects. A catalog queue and exclusive worker lock keep the browser independent
of long model calls. Checkpoint persistence enables restartable review sessions.

## 004: Human annotations do not mutate predictions

A review is an immutable full-label snapshot attached to a run. Effective tags prefer
the reviewed snapshot. Evaluation export always retains the original prediction, so
corrections can measure errors instead of erasing them.

## 005: Managed originals and portable sidecars first

Copy originals and write metadata to app-owned XMP exports. This keeps first-version
writes reversible and format-independent. Embedded writeback and merging existing
sidecars need separate conflict and backup semantics and are deferred.

## 006: Local web UI with a hosting path

FastAPI, Jinja, CSS, and minimal JavaScript keep installation Python-focused. UI handlers
call application services and never perform inference. Host on localhost first. Add
hosting-specific authentication, storage, and connectivity later without rewriting the
annotation model or workflow. A separate React application is unnecessary for v1.

## 007: Explicit unknowns in Jev questions

Use Choice instead of binary-only context decisions because false and unknown have
different annotation meanings. Retain raw distributions and confidence rather than
forcing other providers to produce invented comparable scores. Image support is an
explicit experimental opt-in; model/version is configurable and snapshotted.

## 008: Prove the workflow before cloud integrations

Local files plus browser uploads exercise the complete lifecycle. Google Photos Picker,
iCloud-specific ingestion, semantic indexing, and prompt optimization are later milestones.
No unimplemented connector is advertised as usable.
