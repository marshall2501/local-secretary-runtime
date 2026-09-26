# Agent instructions — Personal Local Secretary AI runtime

## Read before acting
Fetch the design repository's current [START_HERE](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/00_Project/START_HERE.md), [Vision](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/00_Project/Vision.md), [target architecture](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/01_Architecture/SecretaryCore-vNext.md), and [adaptive acceptance plan](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/03_Workflows/PrototypeVerticalSlice.md) before choosing coding, tooling, migrations, or user operations. Reassess latest goal, priority and real-machine status at each handoff.

## Goal and work-unit gate
Build a useful, domain-neutral personal secretary that can use past experience and new observations to select its next action, research, plan, ask, safely execute, verify, record and continue. Game/PC troubleshooting is only one illustration. For each work unit explain its contribution to the ultimate goal, why it takes priority now, and the concrete new user-facing capability. Then identify the behavioral change, failure condition, sub-PC test and what new result would change the agent's subsequent action. A deterministic sequence that records a simulated result is evidence of a component, not evidence of an adaptive Secretary Core.

## Adaptive choices and safety
Treat a past failure as evidence bound to its conditions, not a permanent global ban. Reconsider when environment, user goals or evidence change. Prefer rules phrased as desired capabilities, decision criteria and permitted action conditions. Check for low-risk repetition without progress. Maintain execution-time boundaries through restricted credentials, approval for consequential operations, protected secrets/private originals, recoverable data and isolated services; keep the independent yt-topic-search untouched.

## Technology choice
Current PostgreSQL, FastAPI, Python, Qwen, Docker and own-agent code are candidates. When the user requests comparison or delegates a technical decision, examine current options including new LLMs, MCP and agent frameworks. No monthly review or automation is requested. Reuse existing memory when useful; converters or a clean rebuild are possible. Preserve backups and a single authoritative source during migration.

## Known implementation boundary (2026-09-26)
On the sub-PC: Qwen3:8b reads a limited SQL memory result and answers with provenance; migration 004 and the fixed simulated task/result path have been independently exercised against the actual database. Adaptive retrieval/research/action choices, actual external tool use, automatic memory write and durable integrated continuation have not been demonstrated. The new `scripts/secretary/adaptive_probe.py` is an **unverified standalone experiment** using local fictional research fixtures, permitted read-only memory search, simulated tools and a local checkpoint. It is not a DB Task, live Web adapter, policy-verified real executor or complete Secretary Core. Run offline tests and a sub-PC model trial before making capability claims.

## Development and reporting
Keep coding changes coherent around a useful capability. Test against multiple domains and changed observations, including contradictions, previously attempted actions, limits and paused/resumed work. Report the difference among repository changes, passing unit tests, real sub-PC execution and integrated success. Give the user ONE local operation at a time and inspect the output before the next. Safeguard the user's secrets, data, backups and other running projects. Update the design repository's START_HERE and CurrentStatus after demonstrated progress.
