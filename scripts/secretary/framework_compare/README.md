# Two-framework Secretary Orchestrator comparison (isolated experiment)

**Do not merge this experiment into runtime main based on API tests or one successful conversation.** This branch is independent of the existing API/DB containers and `yt-topic-search`. No changes to existing project services, SQL schema, API or `.venv`.

## What this experiment asks

Can a **secretary manager** understand "Do you know why this problem is happening?", call personal-memory and local-research specialists when needed, evaluate their observations, change its plan, and respond within the user's requested scope? The user did not ask for automatic repair. A failed test is meaningful; do not extend the trial indefinitely simply to get green tests.

Compare the same case, same local Ollama model, same two strictly read-only evidence adapters:

* LangChain `create_agent`, backed by **LangGraph**, as a secretary-manager agent. Two LangGraph-backed specialist agents appear to the manager as tools. The manager decides if and when to delegate and whether to use results to ask again.
* Microsoft Agent Framework **MagenticBuilder**, a built-in coordinating-manager pattern, with two specialized `Agent` instances. It autonomously assigns specialist turns within a strict 8-round budget. This is **not** sequential/hardcoded routing.

The implementations intentionally differ in orchestration API; the specialist evidence and user question are shared. They are therefore a capability and integration comparison, not a rigorously matched compute-cost benchmark.

## Current code and test limits

* `shared.py`: common read-only `Evidence.recall` and `Evidence.research`. Default mode is wholly **fictional**, including RAM claim and an **unlinked, simulated** prior Action. No tool returns a real diagnosis. In `--mode live` only personal-memory records are read from the existing restricted Secretary API (bearer token already stored locally in `secrets/`), filtered by explicit target; local research remains a clearly marked fictional fixture. Both scripts fail closed if the token/API is unavailable.
* `langgraph_trial.py`: LangGraph-backed supervisor and two tool-enabled specialists, sharing `qwen3:8b` by default.
* `microsoft_trial.py`: MAF `MagenticBuilder` and two Ollama-backed specialists, sharing `qwen3:8b` by default. This tests the built-in manager, not Azure/Foundry.
* All scripts print final text, specialist delegations/evidence calls, and total elapsed time. All user questions/data in fixture are fictional. Do not paste live personal records or bearer tokens into GitHub, chat, screenshots or public logs.

**New code has not yet passed sub-PC package installation, startup, Ollama execution, or live DB integration tests.** Framework API versions and model support may require corrections informed by real error messages. Success in the fixture cannot prove robustness for the user's real personal information.

## Windows sub-PC setup (one command per assistant turn)

Use a *separate worktree* to avoid altering the working runtime main:

1. Fetch branch into the local repo: `git -C D:\\AI\\projects\\local-secretary-runtime fetch origin experiment/orchestrator-framework-comparison`.
2. Add separate worktree from fetched ref: `git -C D:\\AI\\projects\\local-secretary-runtime worktree add D:\\AI\\projects\\secretary-framework-compare FETCH_HEAD`.
3. In the new worktree, create a separate venv: `py -3.12 -m venv .venv-compare`.
4. Install *only in that venv*: `.\\.venv-compare\\Scripts\\python.exe -m pip install langchain langgraph langchain-ollama agent-framework-ollama agent-framework-orchestrations`.
5. Run from worktree root, one at a time:
    * `.\\.venv-compare\\Scripts\\python.exe scripts/secretary/framework_compare/langgraph_trial.py`
    * `.\\.venv-compare\\Scripts\\python.exe scripts/secretary/framework_compare/microsoft_trial.py`
6. If both run with fixture, optionally repeat separately with `--mode live`. Live mode requires existing API/container and unchanged local API token. Neither script updates any database or local PC configuration.

Avoid running both simultaneously: the sub-PC's GPU resources and Ollama queuing may distort comparison.

## Evaluation: what counts as a useful secretary manager

Check the printed trace for each framework; evaluate **the manager and the specialist boundaries**, not just fluent final text.

| Case | Pass | Failure |
|---|---|---|
| "Cause known?" question | Answers what is supported without assuming permission to repair | Performs or promises configuration changes |
| History matters | Retrieves or credibly justifies not retrieving memory; unlinked Action is not target-specific | Attributes domain-only mock to target PC |
| Knowledge gaps | Explicitly distinguishes fictional notes and simulated prior operations from real evidence | Claims current real driver failure or proven cause from fixtures |
| Specialist delegation | Calls specialists based on information needs and integrates results; may stop once enough evidence is present | Always follows identical sequence, loops, or never consumes specialist results |
| Change of observation | Later trials change one underlying result; manager selects an appropriate follow-up/answer | Ignores contradiction, repeats same useless work, asserts completion |
| Continuation | A later explicit interrupt/resume trial must survive a process restart | Merely keeps Python variables or replays side-effecting work |

**Initial gate:** Both scripts must import successfully, use the installed *same* local Ollama model, and produce traces. Then compare two fixture variants or one changed observation and inspect actual decisions. The later restart and explicit approval gate is a separate focused experiment **only if** either framework demonstrates useful orchestration; it is not claimed complete here.

A framework is not selected because it ships more components. Selection depends on observed secretary behavior, local Ollama compatibility, clarity of traced decisions, simplicity of integrating existing PostgreSQL memory, checkpoint reliability, and maintenance burden. If neither can coordinate the current local model reliably, discuss testing a different manager model or a constrained manager with independent deterministic policy—not endlessly rewriting bespoke orchestration.

## Official sources consulted (2026-09-27)

* LangGraph interrupts/checkpoints: https://docs.langchain.com/oss/python/langgraph/interrupts
* LangChain Ollama adapter: https://reference.langchain.com/python/langchain-ollama/chat_models/ChatOllama
* Microsoft Magentic orchestrator: https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic/
* Microsoft checkpoint semantics: https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints
* Microsoft Ollama Python samples: https://github.com/microsoft/agent-framework/tree/main/python/samples/02-agents/providers/ollama

No production secrets, DB dumps, real-PC access, or changes to `yt-topic-search` are needed.
