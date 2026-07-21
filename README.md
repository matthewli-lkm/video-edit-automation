# Video Edit Automation

A local-first backend that turns raw footage plus a plain-language brief into a **reviewable edit plan**, then renders that plan with deterministic media tools.

The first product target is intentionally narrow: talking-head content plus evidence-driven gaming
highlights. It is not trying to replace Final Cut Pro or DaVinci Resolve. A local model or a
deterministic signal scorer decides *what* may be worth keeping; validated code decides *whether the
timeline is legal*; FFmpeg performs the actual edit.

## Current foundation

This initial scaffold provides:

- a localhost-only FastAPI service;
- durable project, asset, plan, and job metadata in SQLite;
- safe local-path imports restricted to configured media folders;
- `ffprobe` metadata extraction;
- a typed `EditPlan` contract with time-bound validation;
- an optional OpenAI-compatible local planner for LM Studio or Ollama;
- independent editing profiles and FPS/MOBA game profiles;
- typed highlight signals with deterministic weighting, buffering, merging, and evidence links;
- capture-neutral session manifests for OBS, ScreenCaptureKit, Windows Graphics Capture, and
  externally recorded files;
- deterministic League of Legends recognition from supplied process/window observations;
- automatic League VOD candidate discovery from audio peaks and focused local HUD OCR;
- persisted, typed gaming-analysis JSON plus one-call evidence-linked highlight-plan creation;
- durable highlight-review sessions with accept, reject, boundary-adjustment, missed-event labels,
  and deterministic evaluation metrics;
- a bounded editor/reviewer workflow that renders previews, samples evidence frames, applies only
  typed evidence-linked corrections, and stops after two review rounds by default;
- clock-synchronized manual bookmarks that can directly create a MOBA highlight plan;
- a Windows OBS companion plus Mac SMB/local inbox monitor with READY, checksum, idempotency, and
  reconnect-safe ingestion;
- audio-track roles plus safe game-only rendering when game and microphone tracks are separate;
- configurable dip-to-black video and audio transitions at every selected highlight cut;
- dry-run FFmpeg command generation and background preview/final rendering;
- unit and media integration tests.

Live OS capture, League telemetry, automatic FPS detectors, transcription, captions, and a timeline
UI are later vertical slices. See the [cross-device OBS guide](docs/CROSS_DEVICE_OBS.md),
[vibe-coding plan](docs/VIBE_CODING_PLAN.md), and
[gaming architecture](docs/GAMING_ARCHITECTURE.md).

## Why this shape

```mermaid
flowchart TD
    A["Raw footage"] --> B["Probe and analyse"]
    B --> C["Transcript + scene facts"]
    C --> D["Local LLM proposes EditPlan JSON"]
    D --> E{"Validator accepts?"}
    E -- No --> D
    E -- Yes --> F["FFmpeg preview"]
    F --> G{"Human approves?"}
    G -- Revise --> D
    G -- Yes --> H["Final export"]
```

The LLM never writes shell commands, arbitrary FFmpeg filters, or source-file paths. It only fills a schema using known asset IDs and timestamps. This boundary is the most important part of the system.

## Quick start

Prerequisites: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), FFmpeg/ffprobe, and Tesseract for
automatic League analysis.

On a future Mac:

```bash
brew install ffmpeg tesseract uv
cp .env.example .env
uv sync --extra dev
uv run uvicorn video_edit_automation.main:app --reload --host 127.0.0.1 --port 8765
```

Then open `http://127.0.0.1:8765/docs` for the interactive API.

Run the checks:

```bash
uv run ruff check .
uv run pytest
```

## First useful workflow

1. Create a project.
2. Import a `.mov` or `.mp4` by local path.
3. Create a manual plan, or supply transcript segments and ask a configured local LLM for one.
4. Inspect the validation report and dry-run command.
5. render a low-resolution preview.
6. Accept or revise the plan, then request the final render.

For gameplay, import a full-session recording and assign separate game/microphone track roles. You
can submit normalized signals directly for any profile, or call the League auto-highlight endpoint
to shortlist frames from audio, read visible HUD announcements locally, persist the evidence, and
create a validated MOBA plan in one operation. See the gaming architecture for the request shape and
known OCR limits.

On Windows, the companion can now discover stable OBS recordings, package them into a shared Ready
folder, and let the Mac inbox automatically verify, copy, probe, and register the League session.
Game audio and microphone should remain on separate tracks. League OCR analysis is now available
after ingestion; automatically triggering analysis and rendering for every READY package remains a
later milestone.

Each automatic League plan returns a `review_session`. Its evidence and candidates survive a
restart, and review decisions can be submitted incrementally through the highlight-review API.
When a multimodal reviewer is configured, a bounded agent workflow can render the current plan,
sample representative preview frames, request an independent typed verdict, create a new validated
plan from supported corrections, and repeat once. Metrics remain provisional until every proposed
candidate has an accept, reject, or adjust decision. YouTube publishing and deletion remain absent.

Manual imports reference source media in place. Capture-inbox imports are copied and checksum
verified into the Mac's project workspace before editing. Source files are never overwritten.

## Local AI on the Mac

The design keeps model choices replaceable:

- **Speech-to-text:** add `whisper.cpp` first; it is offline and optimized for Apple Silicon.
- **Edit planner:** connect LM Studio or Ollama through the same OpenAI-compatible adapter.
- **Highlight reviewer:** point `VEA_REVIEWER_MODEL` at a multimodal OpenAI-compatible model. The
  same Qwen instance can serve editor and reviewer calls sequentially, but each call gets a fresh
  role-specific context.
- **Starting model size:** a good 7B-14B instruct model is enough for structured selection from a transcript. Bigger is not automatically better for frame-accurate editing.
- **Hardware:** the backend works on a 48 GB MacBook Pro, while the previously preferred 64 GB Mac setup gives more room to run transcription, an LLM, proxies, and the API together.

The media pipeline and LLM can also live on separate machines later: keep this Mac app as the controller and point the model adapter at a private local-network API.

## Repository guide

- [Architecture and contracts](docs/ARCHITECTURE.md)
- [Windows OBS to Mac automation](docs/CROSS_DEVICE_OBS.md)
- [Gaming capture, signals, profiles, and audio](docs/GAMING_ARCHITECTURE.md)
- [Vibe-coding roadmap and prompts](docs/VIBE_CODING_PLAN.md)
- [Project coding guardrails](AGENTS.md)
- `src/video_edit_automation/domain/` — stable data contracts and validation results
- `src/video_edit_automation/application/` — use cases and ports
- `src/video_edit_automation/infrastructure/` — FFmpeg, SQLite, paths, and local LLM adapters
- `src/video_edit_automation/api/` — HTTP boundary only

## Non-goals for the first release

- generative video or image creation;
- autonomous publishing to YouTube/TikTok;
- direct control of Final Cut, Premiere, or DaVinci;
- multi-user cloud hosting;
- training a custom video model;
- deleting or overwriting original footage.
