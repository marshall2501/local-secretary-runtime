# P0 PKB Engine Comparison — fictional episodes only

This isolated branch is a **comparison fixture and a validation script**, not an installed Cognee/Graphiti integration. The entire dataset is synthetic. Do not send real personal records, keys, source originals or DB dumps to GitHub or third-party providers.

See the [approved P0 plan](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/06_Research/PKB-Engine-Evaluation.md). Existing runtime main, the live PostgreSQL `secretary` DB, GUI, and the separate `yt-topic-search` project are out of scope.

## Files

- `episodes.json`: 10 original fictional Japanese episodes, 5 PC and 5 RC. Each has a stable fixture source reference, recording time, event time and source type. Two later statements explicitly correct the entity mentioned in an earlier episode.
- `expected.json`: 8 independent questions and source-ID requirements, with two explicit correction mappings. **Never ingest this file into a candidate engine or pass it to its LLM.** The engine only receives `episodes` and its public metadata.
- `check_fixture.py`: Python standard-library validator. It also checks the citation sets of optional answer JSON; it does **not** adjudicate factual correctness or temporal reasoning.

Preflight (read-only, no external dependency):

```powershell
python experiments/pkb/check_fixture.py
```

Once both engines have each produced answers for the 8 questions, save each response in a local Git-ignored file with `results` (array), `question_id`, `answer_text`, `cited_episode_ids` (array of fixture IDs), and optionally `reported_verified_episode_ids`. Then run `--results <local-answer-file>` and **manually** inspect factual answers, source quality, validity time versus record time, corrected entities and unsupported assertions. Treat this automatic output only as citation coverage.

## Experiment sequence and boundaries

1. First inspect current sub-PC DB health and create a verified backup via the existing runtime tools; do not change the live DB. Perform one user operation at a time.
2. For Cognee, begin with the official single-user local embedded backend in an isolated directory/port. Before installation, verify the pinned release and provider settings. Some official materials disagree about the PostgreSQL graph backend's production readiness.
3. For Graphiti, start with its current official quickstart and an isolated Neo4j/FalkorDB-compatible backend, checking whether FalkorDB Lite works on the sub-PC. Graphiti defaults to OpenAI for LLM/embeddings; ensure explicitly chosen local-compatible models do not make external API calls unintentionally.
4. Ingest episodes independently with identical timestamps and source refs, then ask all 8 questions independently with no gold answers in context. Measure startup/dependency burden, ingest latency, retrieval latency, GPU/RAM and false assertions. Re-run a few cases after restart and after corrections.
5. Only after separate tests, evaluate whether connecting both improves accuracy and is maintainable. A historic Cognee/Graphiti integration guide is not proof that the pinned modern version exposes it. Keep PostgreSQL as the live source of truth and compare derivatives by stable source IDs.

**Stop conditions:** another project is affected; a test tries to modify `secretary`; unknown API calls could expose personal data; a third-party tool cannot retain the episode source ID or corrected history; no verified backup when live DB changes would be required. Do not install every candidate merely because it can be installed.
