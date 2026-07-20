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

## Milestone 6 — evaluation and learning loop

**Outcome:** compare planner models/prompts using accepted/rejected segments, correction time, plan
validity, semantic coverage, and render success.

This connects naturally to the separate model-benchmark project discussed previously: that system
can evaluate candidate models, but this repository should only consume the selected provider/model
through configuration. Do not merge a general benchmark platform into the editor.

## Practical schedule

| Week | Deliverable | Proof |
|---|---|---|
| 1 | Foundation + one real import/render | 10-second source becomes a legal preview |
| 2 | Silence removal | synthetic pauses removed within tolerance |
| 3 | Offline transcription | cached Cantonese/English transcript |
| 4 | LLM plan + fallback | golden plan metrics and manual preview |
| 5 | Captions + 9:16 | readable mobile preview |
| 6 | Review UI | revise, approve, export end to end |
| 7 | Evaluation harness | compare two local models/prompts |
| 8 | Packaging and demo | one-command Mac setup and portfolio video |

Treat the schedule as eight focused iterations, not a promise that every advanced editor feature will
exist after eight weeks.

