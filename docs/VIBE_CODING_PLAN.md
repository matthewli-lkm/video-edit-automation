# Vibe-coding plan

## How to use Codex effectively

Vibe coding is useful here if each session has a hard boundary. Ask for one vertical slice, require
tests, inspect the diff, and run a real sample before moving on. Avoid prompts such as "finish the AI
video editor"; they invite a large pile of code whose media timing cannot be trusted.

For every milestone:

1. give Codex the outcome and acceptance criteria below;
2. ask it to read `AGENTS.md` and existing contracts first;
3. let it inspect the current code before proposing files;
4. require unit tests plus one real-media smoke test when applicable;
5. review the generated API/schema before accepting implementation;
6. commit only that milestone.

## Milestone 0 — foundation (this scaffold)

**Outcome:** create projects, import/probe local media, store metadata, create and validate typed
plans, dry-run or execute an FFmpeg render, and optionally call a local structured-output LLM.

**Exit check:** tests and lint pass; source footage cannot be overwritten; invalid times never reach
FFmpeg.

## Gaming foundation — implemented

**Outcome:** represent FPS and MOBA policies without forking the backend, normalize heterogeneous
highlight evidence, build deterministic evidence-linked plans, and export game-only audio when the
recording contains a separately identified game track.

**Implemented checks:**

- editing intent and game genre are separate profiles;
- built-in generic FPS/MOBA weights, buffers, thresholds, and capture/audio policies are typed;
- nearby signals merge into one candidate and retain evidence IDs;
- unknown assets and out-of-bounds signals fail before plan creation;
- game-only rendering selects only an explicit game track;
- mixed audio fails visibly instead of promising unreliable microphone removal.

See [the gaming architecture](GAMING_ARCHITECTURE.md) for the capture and detector roadmap.

## Gaming milestone G1a — capture-neutral sessions (implemented)

**Outcome:** register an OBS/native/external recording using one manifest, recognize League from
supplied process/window evidence, apply audio roles, synchronize manual bookmarks, and generate a
MOBA highlight plan without platform-specific backend code.

**Implemented checks:**

- OBS is supported as a recorder identity on Windows or macOS;
- platform-specific native recorder identities are rejected on the wrong OS;
- unknown games remain unknown and can be explicitly overridden;
- League observations choose `generic_moba` with recorded confidence/evidence;
- bookmarks accept media time or the session's monotonic clock;
- capture sessions and signals persist in SQLite;
- a saved bookmark creates an evidence-linked highlight plan.

## Gaming milestone G1b — cross-device OBS discovery (implemented)

**Outcome:** a Windows companion discovers stable OBS recordings and writes verified READY packages;
the Mac automatically ingests them from mounted SMB/local folders into managed local storage.

**Implemented checks:**

- recordings must remain unchanged for a configurable settle period;
- `READY` is written only after recording and manifest creation;
- the Mac rejects traversal, symlinks, size mismatches, and checksum failures;
- verified files are atomically promoted from `.partial` into project `imports/`;
- deterministic session IDs make repeated scans idempotent;
- missing SMB mounts are reported and retried without crashing the API;
- automatic monitoring is optional and localhost API status/manual scan endpoints remain available.

## Gaming milestone G1c — event and native capture adapters

**Outcome:** collect synchronized League events on Windows, add a foreground-process observer, and
only then consider optional ScreenCaptureKit or Windows-native recording helpers.

## Gaming milestone G2 — automatic signal adapters

**Outcome:** produce normalized signals automatically, starting with one supported FPS and one MOBA.

**Acceptance criteria:**

- telemetry/log/replay integrations stay behind per-game adapters;
- generic audio, microphone-reaction, motion, scene, and OCR detectors work without a game adapter;
- every signal records source and confidence;
- analysis can use the microphone even when export is game-only;
- a labelled test set measures missed and false highlights per game and profile.

### G2a — League OCR/audio baseline (implemented)

The first MOBA vertical slice now shortlists likely moments from FFmpeg audio peaks, adds dense
endgame coverage, reads standard English League announcements with local Tesseract, and normalizes
kills, deaths, team fights, objectives, structures, streaks, multikills, and match results. It saves
typed analysis JSON and creates a validated plan through one API request. Unit tests use no live
model or network, and the supplied 696-second spectator VOD passed analysis and rendering smoke
tests.

This is not completion of all G2 acceptance criteria. A specific FPS adapter, microphone-reaction,
motion and scene detectors, a labelled evaluation set, and player-perspective attribution remain.

### G2b — League review/evaluation foundation (implemented)

Every automatic League plan now creates a durable review session anchored to the exact analysis,
source fingerprint, game profile, evidence signals, selected candidates, and edit plan. The API can
accept, reject, revise candidate boundaries, or record a missed event without changing the original
media or silently rewriting earlier decisions. It reports provisional/completed candidate metrics,
accepted duration, boundary correction, correction rate, and missed-event counts. Building a useful
labelled dataset still requires reviewing multiple real VODs; detector-level signal accuracy and the
visual review UI remain later slices.

### G2c — bounded editor/reviewer orchestration (implemented)

An automatic League review session can now start a durable preview/review loop. Each round creates
a unique validated preview, packages selected and unselected evidence with bounded frame samples,
and accepts only a strict reviewer verdict. Evidence-supported corrections create a new validated
`EditPlan` version; invented references, invalid ranges, overlaps, evidence-free additions, no-op
changes, render failures, and excess review rounds stop safely for human review. A schema-bound
multimodal OpenAI-compatible adapter can later point at Qwen directly or through Hermes; tests use
scripted reviewers and no network or running model. Publishing and cleanup are deliberately out of
scope.

## Milestone 1 — deterministic silence editor

**Outcome:** analyse a single talking-head video for silence and create a plan that removes silence
longer than a configurable threshold while keeping short natural pauses.

**Acceptance criteria:**

- output analysis is saved as typed JSON;
- thresholds are explicit (`minimum_silence`, `padding_before`, `padding_after`);
- adjacent keep-ranges are merged;
- no segment is shorter than the configured minimum;
- a synthetic video test proves the resulting duration;
- the feature works with the LLM disabled.

**Suggested Codex prompt:**

> Read AGENTS.md and the architecture. Implement Milestone 1 only. Add an FFmpeg silencedetect
> analyser behind a port, convert detected silence into deterministic keep-ranges, save the analysis,
> and expose one API endpoint that creates a draft edit plan. Do not add transcription or UI. Add unit
> tests for interval arithmetic and an FFmpeg integration test using generated media.

## Milestone 2 — offline transcription

**Outcome:** create timestamped transcript segments using `whisper.cpp`, with progress and cached
results.

**Acceptance criteria:**

- adapter takes a media asset and returns domain transcript segments;
- audio is converted to 16 kHz mono PCM in the project cache;
- provider/model/version are recorded for reproducibility;
- rerunning unchanged media uses the cache;
- Cantonese/English mixed speech is retained without forced translation;
- tests use a fake transcriber; one optional local smoke test uses the real binary.

**Suggested Codex prompt:**

> Implement Milestone 2 behind a Transcriber port. Keep whisper.cpp CLI details inside one adapter.
> Record model and command provenance, cache by asset fingerprint plus settings, and never require a
> network download at runtime. Do not change EditPlan fields unless a failing use case requires it.

## Milestone 3 — transcript-aware local LLM planner

**Outcome:** a brief such as "make a 60-second vertical summary for data-science students" returns a
valid, evidence-linked plan.

**Acceptance criteria:**

- only known transcript segments and asset IDs are sent to the model;
- response uses `EditPlanDraft` JSON schema and temperature zero;
- every selected range maps back to transcript evidence;
- invalid output gets one repair attempt, then fails visibly;
- deterministic fallback can rank transcript chunks without an LLM;
- golden transcript tests compare coverage, duration, and invalid-range rate.

**Suggested Codex prompt:**

> Strengthen the existing local planner for Milestone 3. Add transcript chunking, evidence IDs, one
> schema-repair attempt, and a deterministic extractive fallback. Build golden tests from small fixed
> transcripts; do not call a live model in CI. Report coverage and plan-validity metrics.

## Milestone 4 — captions and vertical previews

**Outcome:** render 9:16 previews with readable captions and safe-area-aware framing.

**Acceptance criteria:**

- caption timings are derived from kept transcript ranges after cuts;
- ASS/SRT generation is deterministic and testable;
- fonts and safe margins are configured, not model-generated;
- original landscape composition remains available as a fallback;
- preview renders are low resolution and uniquely named.

## Milestone 5 — human review UI

**Outcome:** a small Mac-friendly web UI shows transcript, selected ranges, warnings, and preview;
users can include/exclude or trim segments before final export.

**Acceptance criteria:**

- UI edits the domain plan rather than FFmpeg text;
- every revision creates a new plan version;
- final render requires explicit approval;
- progress survives page refresh;
- keyboard-first controls cover normal review.

### G2d — gaming review console (implemented)

The gaming workflow now has a compact local-first console with source and project selection,
plain-language editing instructions, preview playback, evidence-linked candidate decisions,
boundary adjustment, Agent 2 status, versioned human revisions, explicit approval, and final-render
requests. Backend state restores after refresh; realistic demo data keeps the interface testable
before a local model is available.

The review surface separates full-source orientation, the selected generated region, and the
rendered reel. A source overview shows every detected candidate in recording time, while a zoomed
current-cut view highlights the exact In/Out region, supports draggable and typed `MM:SS.s`
boundaries, and provides direct boundary checks.

Human approval is stored separately from reviewer-agent approval. It is tied to the current plan ID
and version, any revision invalidates it, and final rendering remains blocked until the revised plan
is approved. Publishing, cleanup, live capture, vertical output, and multi-category runtime recipes
remain later milestones.

### G2e — dashboard integration and correctness (implemented)

The deployed review console now lives in the main repository with the backend. A fresh checkout can
install the frontend, generate API types from FastAPI, and start both local processes with one
script.

The workflow display is derived from actual source, analysis, decision, approval, and final-render
state. The player has explicit whole-source, current-cut, and generated-reel modes; a compact source
scrubber sits directly beneath it; evidence rows seek to their timestamps; and the trim view has
unsaved-state protection, reset, undo, and redo. Candidate changes require a new plan version before
approval or final rendering. Agent 2 remains optional, and final output actions appear only after a
successful render.

Browser-compatible proxy generation, render-job restoration after application restart, and
duplicate-job protection are delivered by the separate G2f milestone below.

### G2f — media and refresh reliability (implemented)

Browser-incompatible recordings now receive a managed H.264/AAC MP4 proxy while the original
remains untouched and authoritative for analysis and final rendering. The playback route supports
HTTP byte ranges, proxy paths are revalidated inside the project workspace, and repeated
preparation requests reuse the same asset-fingerprint-and-settings job.

Render jobs can be listed per project and identical plan/preset requests reuse active or completed
managed output. The dashboard restores the exact selected recording, newest review/workflow,
preview/final jobs, polls active jobs, and shows proxy preparation, failure, and retry states. Final
downloads use an attachment response owned by the backend. On application restart, queued or
running proxy/render records become visibly recoverable failures rather than remaining stuck.

Desktop process integration, a native file picker, Finder actions, dependency diagnostics, and
installer packaging remain the separate G2g application-integration milestone.

### G2g — desktop application integration (implemented)

The sandboxed Electron shell owns both development and packaged process lifecycle. It reserves
loopback ports, starts and health-checks the backend and production dashboard, connects them
automatically, and shuts them down with the app. Packaged builds contain a single-file Python
sidecar and the dashboard runtime, so they do not depend on a user-installed Python or Node.js. A
narrow preload bridge provides a native video picker constrained to configured media roots and a
Finder reveal action constrained to successful MP4 output inside the managed project workspace.

The dashboard includes a typed dependency diagnostic for the database, writable workspace, free
space, FFmpeg, ffprobe, and optional Tesseract. Batch 3 produces an ad-hoc-signed `.app` and `.dmg`
for local testing. Bundling media binaries and their licences, application branding, Developer ID
signing, notarization, updates, and clean-Mac release qualification are the Batch 4 release boundary.

### Pre-Batch 4 — workflow separation (implemented)

The console now exposes Manual and deterministic Kill & teamfight detection as first-cut workflows,
with AI assistance stored as a separate Off/Review setting. Manual ranges create typed manual
evidence and a validated plan, but exact human-trimmed ranges skip the redundant automatic-plan
approval screen. Manual editing marks and includes plays, trims each selected play with direct
manipulation or precise timestamps, then exports a combined reel, separate clips, or both. The deterministic workflow has no
natural-language field: it accepts only
Tesseract-detected champion kills, multikills, and kill clusters, then merges nearby action into
teamfight clips. Full local-model editing remains deliberately deferred.

Desktop onboarding now has only two mutually exclusive choices: create a new managed clips project
or open an existing video folder in Finder. Both enter the editing room immediately. Empty projects
prompt for their first recording inside the editor, and active projects can add further recordings
there. New projects appear under `Desktop/Cutroom Projects/<project name>` and finished MP4s stay in
that project's `Exports` folder. The dashboard also avoids restoring through a stale default backend port and translates
loopback connection failures into a useful retry message.

## Milestone 6 — evaluation and learning loop

**Outcome:** compare planner models/prompts using accepted/rejected segments, correction time, plan
validity, semantic coverage, and render success.

This connects naturally to the separate model-benchmark project discussed previously: that system
can evaluate candidate models, but this repository should only consume the selected provider/model
through configuration. Do not merge a general benchmark platform into the editor.

The gaming path now has the backend label, metric foundation, and review console for this milestone.
Model/prompt comparisons, correction-time capture, and aggregate reports across VODs are not yet
implemented.

## Practical schedule

| Week | Deliverable | Proof |
|---|---|---|
| 1 | Foundation + gaming contracts | real import, legal preview, scored FPS/MOBA plan |
| 2 | Silence editor or live capture G1b | synthetic pauses or separate-track session |
| 3 | Offline transcription or signals G2 | cached transcript or automatic evidence |
| 4 | LLM plan + fallback | golden plan metrics and manual preview |
| 5 | Captions + 9:16 | readable mobile preview |
| 6 | Review UI | revise, approve, export end to end |
| 7 | Evaluation harness | compare planners and gaming profiles |
| 8 | Packaging and demo | one-command Mac setup and portfolio video |

Treat the schedule as eight focused iterations, not a promise that every advanced editor feature will
exist after eight weeks.
