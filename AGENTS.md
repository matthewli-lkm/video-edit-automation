# Coding instructions for agents

Read `README.md`, `docs/ARCHITECTURE.md`, and the current milestone in
`docs/VIBE_CODING_PLAN.md` before changing code.

## Product boundary

This is a local-first, single-user video automation backend. Supported foundations cover spoken
content and evidence-driven gaming highlights. Do not broaden a milestone into a general
non-linear editor.

## Invariants

1. An LLM may return only typed domain data, currently `EditPlanDraft`.
2. Never execute shell text, source paths, filtergraphs, or output paths produced by an LLM.
3. Pass subprocess arguments as a list and keep `shell=False`.
4. Resolve imported paths and require them to be inside configured media roots.
5. Never overwrite or delete original footage.
6. Render only into the per-project workspace with a unique job ID.
7. Validate every plan against the repository's real asset durations before rendering.
8. Keep API, application, domain, and infrastructure dependencies pointing inward.
9. Prefer a small vertical slice with tests over a wide unfinished subsystem.
10. Do not add a message broker, vector database, or cloud dependency until a measured need exists.
11. Keep capture/OS APIs behind adapters; Python application code consumes typed session and signal
    contracts.
12. Keep editing intent, game genre, and specific-game adapters separate; do not fork the backend by
    content category.
13. Never claim game-only audio is possible from a mixed game/microphone track. Require a separately
    identified game track or fail visibly.
14. Every automatically selected gaming segment must retain the signal IDs that justify it.
15. Treat OBS, ScreenCaptureKit, Windows Graphics Capture, and external files as adapters to one
    capture-session contract; never fork editing behavior by operating system.
16. A registered capture session does not imply that this backend performed or controlled capture.
17. Never ingest a shared-folder recording without `READY`, containment checks, declared size, and
    checksum verification; copy it to managed local storage before probing or rendering.
18. Treat missing SMB mounts and partial packages as recoverable states, and make package ingestion
    idempotent by stable session ID.

## Definition of done

- The requested behavior has unit tests.
- `uv run ruff check .` passes.
- `uv run pytest` passes.
- Media-command changes include a dry-run assertion; rendering changes include an integration test
  when FFmpeg is available.
- Documentation and `.env.example` reflect new configuration.
- No test needs network access or a running LLM.

## Change size

Keep one milestone or one behavior per pull request. Before writing code, state:

- the user-visible outcome;
- the domain contract affected;
- the files expected to change;
- the tests that will prove it works.
