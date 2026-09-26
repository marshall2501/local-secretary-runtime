# Agent instructions — Secretary Runtime

**Before editing this repository, read the design repo's latest** [START_HERE](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/00_Project/START_HERE.md), [new target architecture](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/01_Architecture/SecretaryCore-vNext.md), and [CurrentStatus](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/00_Project/CurrentStatus.md). Do not rely solely on an old implementation checklist or on this mirrored summary if the design repo is newer. If unable to access the design repo, ask for the latest START_HERE instead of silently using stale priorities.

## Purpose and immediate priority
This is a **cross-domain personal secretary AI**, not a database/archive project. The prototype should first show that a USER can ask a natural-language question to one real LLM, which queries existing PostgreSQL memory and produces a grounded response. Next integrate existing task ID, plan, safe simulated action, verification, automatic result recording and continuation. Each task must answer: what new end-to-end secretary ability will become possible?

2026-09-26 status: on the sub-PC the fixed-step `POST /prototype/tasks` and persisted four-step task creation passed real DB test (Task ID `af6d43d1-23e8-4ee2-b495-699e4792851b`). Later `POST /prototype/tasks/{task_id}/run` code, migration `004_prototype_tool_sources.sql`, and five unit tests exist on main; five tests passed on the sub-PC. **Migration 004 is not applied and the new mock-run API is not rebuilt/real-DB-tested.** The user deliberately paused that implementation-first loop. Do not resume 004 reflexively: FIRST connect one viable LLM on the sub-PC to `GET /memory/search` via a minimal CLI/HTTP demo. Treat the model's output as untrusted and cite retrieved memory with verification caveats.

## Operational rules
- Use existing PostgreSQL, FastAPI, Python, Docker Compose. Avoid premature new frameworks, containers, manual archival, n8n, model routers or wholesale schema changes. Keep existing DB/API compatibility.
- Sub-PC primary, main-PC manual recovery only. No dual-master DB. Never alter or stop `yt-topic-search` containers, volume, network or DB.
- Preserve the restricted `secretary_api` role. Do not send secrets, personal originals or DB dumps to Git/chat. External high-impact actions require independent approval, separate from routine memory recording.
- Git: user prefers main for small edits, a single reused `work` branch if necessary. Check status before modifying local files. Back up DB before schema changes.
- USER LOCAL OPERATIONS: one command/action then wait for output. ENGINEERING WORK: bundle edits into a meaningful capability and report what actually became possible. Differentiate unit tests, main repo commits, real sub-PC execution, and untested main-PC fallback.
- Update design repo START_HERE and CurrentStatus when a meaningful milestone passes, and reread START_HERE when a new chat or coding agent takes over.
