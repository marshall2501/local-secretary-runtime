# Secretary Core: runnable manager (experimental branch)

The first executable Secretary Core is scripts/secretary/secretary_core.py.
It uses LangGraph StateGraph, the existing local Ollama client and the
restricted Secretary API. This is not the previous two-framework comparison
script, and it is not an automatic PC repair tool.

**Currently implemented:** manager chooses memory/research delegation, answer
or a question; read-only dispatch binds queries to the named user target;
independent structural evaluation rejects unrelated entities and distinguishes
simulated/unlinked results; a separate answer-review LLM call compares the
proposed final answer to the actual observations; feedback returns to manager
for reassignment or cautious answer. Original request, action trace,
observations and feedback persist atomically under ignored local secrets
folder. Explicit question pause and same-task resume work after process
restart. Manager steps and specialist calls are strictly bounded.

**Not implemented:** adaptive events written to the actual PostgreSQL
Task/Action/Result tables, live web research, real-PC diagnostics, memory
auto-write, autonomous external operations or scheduled background follow-up.
The first version uses a local UUID and JSON checkpoint; it must not be
reported as a DB-backed integrated production secretary. Live memory reads
use the existing API, but real-web research fails closed rather than
substituting fictional documents.

## On the sub-PC

Update ONLY the isolated comparison worktree:

    git -C D:\AI\projects\secretary-framework-compare pull --ff-only origin experiment/orchestrator-framework-comparison

Offline behavior tests in the already installed LangGraph environment:

    D:\AI\projects\secretary-framework-compare\.venv-compare\Scripts\python.exe -m unittest discover -s D:\AI\projects\secretary-framework-compare\tests -p test_secretary_core.py -v

Then run the local Ollama manager with clearly fictional data:

    D:\AI\projects\secretary-framework-compare\.venv-compare\Scripts\python.exe D:\AI\projects\secretary-framework-compare\scripts\secretary\secretary_core.py "架空テストPCで架空ゲームAが突然終了します。以前の経験から原因わかる？" --mode fixture

The JSON response includes trace, answer, review outcome, local task ID
and checkpoint path. For a paused conversation, start a new process with
--resume TASK_ID --reply "new information" in place of the question.

The optional live read-only-memory mode requires the existing API at
127.0.0.1:8010. Set environment variable LSA_SECRETARY_API_TOKEN_FILE to the
absolute path of the existing token in the original runtime worktree.
Then run with --mode live --target "exact entity name" --domain pc.
Do NOT copy the bearer token, DB dumps, private records or the checkpoint
into GitHub or chat. It is stored under the already ignored secrets/ path.

## Acceptance

One relevant question must cause the manager to get existing past experience
when needed, distinguish unlinked simulation from target-specific evidence,
ask or consult the second specialist when the first result is insufficient,
and answer with appropriately limited claims. A review failure must return
control to the manager rather than stopping at a fluent unsupported answer.
A waiting-user task must resume with its original objective after restart.

The deterministic tests and experimental GitHub Actions workflow check
wiring and these transitions with a scripted manager. Only the sub-PC Ollama
trial can establish whether qwen3:8b actually demonstrates those judgments.
Do not merge the branch into main based on syntax tests alone.

## 操作可能なGUI（2026-09-27追加）

最初のローカルNiceGUI画面を実験ブランチへ追加。依頼入力、checkpointからの依頼履歴、判断・委任・Python評価・独立監査の経過、証拠と出典フラグ、結論、ask_user質問への回答・同一Task再開を扱う。実ブラウザとQwen3によるGUI一気通貫試験は未検証。詳細とサブPC手順は docs/secretary-gui.md 。
