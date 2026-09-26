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

## Why this change comes first\n\nThe initial sub-PC Qwen3:8b run varied the next tool choice but repeated an unhelpful research query, so model comparison is now part of the adaptive-decision experiment. Reuse the identical fixtures and evaluate whether the selected model handles missing information, recent evidence, repeated actions and an appropriate unresolved outcome. The same Ollama `/api/chat` endpoint accepts the model tag; no second serving stack is needed. This is a model selection mechanism, not automatic performance-based routing. Prompt and model-specific thinking parameters still require separate comparability testing.\n\n## Current limits

- The research adapter searches **fictional local documents only**; it has no live web access. The observation tools return **fictional fixture data**, not live PC, stock or other state.
- SQL Memory API search is bounded, and the Ollama model can choose inappropriate search strings or next actions; the actual behavior has to be tested on the sub-PC.
- This trial has its own local `trial_id`, not a PostgreSQL Task ID. An `awaiting_verification` state marks a model's completion proposal rather than independent success. User replies, task checkpoint durability after process restart, DB memory auto-write, approval for consequential actions and true end-to-end goal verification require integration after the judgment experiment is evaluated.
- Keep the game and shopping state paths distinct; each state is tied to one fictional fixture. Only one sub-PC command should be requested from the user at a time.

## Acceptance observations

Compare the two domains and perturb the scripted fixture outcomes or past memory records. A useful result must show the model using previous observations, skipping irrelevant repetitions, changing research or tool choices when evidence changes, and retaining its original goal after setbacks. Record raw decision traces and any wrong turns; do not call this an integrated Secretary Core based only on clean fixture paths.
