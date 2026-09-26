# Adaptive decision probe — first cross-domain experiment

This is a **standalone experimental decision loop**, not a complete Secretary Core. It lets a selected local Ollama model (default `qwen3:8b`) choose the next action based on previous observations, using the existing read-only Memory API, fictional local research documents and simulated observation tools. It records each decision, rationale, observation and retry in an atomic local checkpoint. Different fixtures (game and shopping) use the same engine. It deliberately cannot perform real external actions or independently certify the user's goal as complete.

## On the sub-PC

Pull the latest runtime main. Then run the offline tests with `python -m unittest discover -s tests -p test_adaptive_probe.py -v`. When they pass and the existing Ollama service is available, use ONE of the following experiments:

```powershell
python .\scripts\secretary\adaptive_probe.py --scenario .\scripts\secretary\fixtures\adaptive_game.json --state .\secrets\adaptive-game.json --steps 5
```

```powershell
python .\scripts\secretary\adaptive_probe.py --scenario .\scripts\secretary\fixtures\adaptive_shopping.json --state .\secrets\adaptive-shopping.json --steps 5
```

Select an already installed Ollama model with `--model MODEL_TAG`. For example, after downloading `gemma4:12b` using Ollama, run the same fixture with `--model gemma4:12b --state .\\secrets\\adaptive-game-gemma4.json`. **Each model and scenario needs its own state file** so the results and histories remain comparable. Existing checkpoints from before the model option are treated as `qwen3:8b`. The CLI rejects attempts to resume one model's checkpoint with a different model. You can set the default using `LSA_OLLAMA_MODEL` as well. The existing read-only `ask.py` also accepts that environment variable; its fallback remains `qwen3:8b`. The actual model name is saved inside each experiment checkpoint. The model must be installed locally in the connected Ollama instance; choosing a tag does not download it.\n\nEach command can be repeated to resume its own checkpoint. The script stops on an additional question, waiting state, or proposed completion. To answer a pending question, rerun the same command with `--reply "your answer"`. State files belong in `secrets/`, which is Git-ignored; the checkpoint can include retrieved private memory. Inspect the printed decisions to see whether new evidence actually changes the next action. A generic language-model response or an endlessly repeated low-risk step is an experimental failure, not a success.

## Why this change comes first\n\nThe initial sub-PC Qwen3:8b run varied the next tool choice but repeated an unhelpful research query, so model comparison is now part of the adaptive-decision experiment. Reuse the identical fixtures and evaluate whether the selected model handles missing information, recent evidence, repeated actions and an appropriate unresolved outcome. The same Ollama `/api/chat` endpoint accepts the model tag; no second serving stack is needed. This is a model selection mechanism, not automatic performance-based routing. Prompt and model-specific thinking parameters still require separate comparability testing.\n\n## Read-only past-action recall (new code, not yet sub-PC validated)

When the LLM selects `memory_search`, the probe now retrieves the existing SQL memory results **and** previously saved Action/Result records via the authenticated read-only `/experience/search` API. Each experience records the task request, actual operation, outcome, source, whether it was simulated, and the association to the currently named entity. Older prototype tasks without `entity_id` are labelled `domain_only_unlinked`: a shared PC domain does **not** prove an action was taken on a named PC. Other specifically named entities are excluded. The regular read-only `ask.py` answer CLI retrieves the same history for past-attempt questions. This makes prior results available to reasoning, but does **not** force the model to choose `memory_search` or independently verify an answer.

On the sub-PC, pull runtime main and run both the adaptive tests and the new `test_experience_recall.py` tests before rebuilding the existing secretary API container. **Until that container is rebuilt with the new endpoint, experience lookups cannot work**. If testing prints private memory or prior task requests, redact them before sharing traces; checkpoints remain only in git-ignored `secrets/`. This is not yet a DB-persisted adaptive Task or a real diagnostic.

## 2026-09-27: Evidence-first recall for goals that require earlier experience

The real sub-PC Gemma 4 12B trial chose crash-log inspection, two fictional local documents, then `propose_complete` **without ever choosing memory_search**. The recently tested read-only Action/Result API was therefore never called. The model also described a driver-version association more strongly than the observations justified; a driver timeout alone does not establish the cause.

For goals explicitly requiring previous experience, the fictional game fixture now sets `initial_recall: true`. The probe obtains the existing SQL memories and bounded past Action/Result observations once **before the first LLM decision**, saves them in its private checkpoint, and makes them available in every subsequent decision context. The model still decides whether to investigate further, request additional memory, ask the user, or stop; this is an evidence preparation rule, not a forced diagnostic workflow. A shopping goal that does not require history does not automatically fetch it. Results for other named entities are filtered, and same-domain unlinked results remain labelled as unrelated-to-any-confirmed-entity; simulated results must not be treated as real-PC diagnoses. If the local Memory API is unavailable, the experiment stops rather than silently proceeding as though no history existed. On checkpoint resume, the initial read is not repeated; production DB Task resumption will separately need freshness checks.

**Code and two new offline tests are on GitHub only; the updated probe and new LLM behavior are not yet sub-PC validated.** Use a new git-ignored `--state` file for another run; previous `awaiting_verification` trials are retained as historical evidence, not silently overwritten. The new initial recall is still a limited read-only experiment, not a persistent unified Task.

## Current limits

- The research adapter searches **fictional local documents only**; it has no live web access. The observation tools return **fictional fixture data**, not live PC, stock or other state.
- SQL Memory API search is bounded, and the Ollama model can choose inappropriate search strings or next actions; the actual behavior has to be tested on the sub-PC.
- This trial has its own local `trial_id`, not a PostgreSQL Task ID. An `awaiting_verification` state marks a model's completion proposal rather than independent success. User replies, task checkpoint durability after process restart, DB memory auto-write, approval for consequential actions and true end-to-end goal verification require integration after the judgment experiment is evaluated.
- Keep the game and shopping state paths distinct; each state is tied to one fictional fixture. Only one sub-PC command should be requested from the user at a time.

## Acceptance observations

Compare the two domains and perturb the scripted fixture outcomes or past memory records. A useful result must show the model using previous observations, skipping irrelevant repetitions, changing research or tool choices when evidence changes, and retaining its original goal after setbacks. Record raw decision traces and any wrong turns; do not call this an integrated Secretary Core based only on clean fixture paths.
