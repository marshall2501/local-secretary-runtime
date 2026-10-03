"""Local-only fictional experiment runner. No PostgreSQL or cloud access.

This module does not import any UI framework. Only bundled PC/RC fixtures and
models already installed on localhost Ollama can enter a run. SQLite holds
experiment metadata outside the source tree, never PKB Claims or model thinking.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from infrastructure.async_runtime.background_jobs import SerialBackgroundExecutor
from .diagnostic_cases import MODES, build_ollama_payload, judge_response
from .fictional_fixture import load_fictional_episodes
from pkb.extraction_service import Extraction, inspect_model_output
from .gui_helpers import analyze_reply

FIXTURE = Path(__file__).parent / "fixtures" / "episodes.json"
OLLAMA = "http://127.0.0.1:11434"
MODEL_PATTERN = re.compile(r"^[a-zA-Z0-9._:/-]{1,100}$")
STATES = frozenset(("queued", "running", "completed", "check_failed",
                    "timeout", "connection_error", "error", "interrupted"))
ACTIVE = ("queued", "running")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunStore:
    """One SQLite database outside the repo; each operation owns its connection."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT,
                status TEXT NOT NULL, episode_id TEXT NOT NULL,
                mode TEXT NOT NULL, model TEXT NOT NULL,
                predict INTEGER NOT NULL, think TEXT NOT NULL,
                payload TEXT NOT NULL, result TEXT, error TEXT
            )""")
            # Once this process starts, a previous process cannot still own
            # queued/running work if the app binds its exclusive localhost port.
            db.execute("""UPDATE runs SET status='interrupted',
                error='Application restarted before run finished',
                finished_at=? WHERE status IN ('queued','running')""", (utc_now(),))

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        item["result"] = json.loads(item["result"]) if item["result"] else None
        return item

    def insert(self, run: dict) -> None:
        with self.lock, self._connect() as db:
            db.execute("""INSERT INTO runs
                (id,created_at,status,episode_id,mode,model,predict,think,payload)
                VALUES (:id,:created_at,:status,:episode_id,:mode,:model,
                        :predict,:think,:payload)""", {
                **run, "payload": json.dumps(run["payload"], ensure_ascii=False),
            })

    def update(self, run_id: str, status: str, *, result=None, error=None) -> None:
        if status not in STATES:
            raise ValueError("Unknown run status")
        started = utc_now() if status == "running" else None
        finished = utc_now() if status not in ACTIVE else None
        with self.lock, self._connect() as db:
            db.execute("""UPDATE runs SET status=?, started_at=COALESCE(?,started_at),
                finished_at=COALESCE(?,finished_at), result=?, error=?
                WHERE id=?""", (status, started, finished,
                              json.dumps(result, ensure_ascii=False) if result is not None else None,
                              error, run_id))

    def get(self, run_id: str):
        with self.lock, self._connect() as db:
            return self._decode(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def list(self, limit: int = 50):
        limit = max(1, min(100, int(limit)))
        with self.lock, self._connect() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
            return [self._decode(r) for r in rows]

    def active_count(self) -> int:
        with self.lock, self._connect() as db:
            return int(db.execute("SELECT count(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0])


class ExperimentRunner:
    """Single queued worker: browser lifecycle cannot cancel a submitted run."""

    def __init__(self, store: RunStore, *, fixture: Path = FIXTURE, transport=None):
        self.store = store
        self.episodes = {ep["id"]: ep for ep in load_fictional_episodes(fixture)}
        self.pool = SerialBackgroundExecutor("pkb-web-workbench")
        self.transport = transport or self._call_ollama
        self.lock = threading.Lock()

    @staticmethod
    def installed_models():
        with urlopen(OLLAMA + "/api/tags", timeout=5) as response:
            data = json.load(response)
        return sorted({m["name"] for m in data.get("models", [])
                       if isinstance(m, dict) and isinstance(m.get("name"), str)})

    @staticmethod
    def _call_ollama(payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(OLLAMA + "/api/chat", data=body, method="POST",
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=240) as response:
            data = json.load(response)
        if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
            raise ValueError("Ollama response has no message object")
        return data

    def submit(self, *, episode_id: str, mode: str, model: str,
               predict: int = 1100, think: str = "自動", verify_model: bool = True) -> dict:
        if episode_id not in self.episodes or mode not in MODES:
            raise ValueError("Unknown fictional episode or diagnostic mode")
        if (not isinstance(model, str) or not MODEL_PATTERN.fullmatch(model)
                or "://" in model):
            raise ValueError("Invalid model name")
        if type(predict) is not int or predict not in (1100, 2048, 4096):
            raise ValueError("Unsupported token limit")
        if think not in ("自動", "無効"):
            raise ValueError("Invalid thinking setting")
        # Never auto-pull models, send to a cloud endpoint or accept arbitrary texts.
        if verify_model and model not in self.installed_models():
            raise ValueError("Model is not installed locally")
        payload = build_ollama_payload(self.episodes[episode_id], model,
                                       predict, think, mode)
        with self.lock:
            if self.store.active_count() >= 20:
                raise ValueError("Too many queued experiments")
            run = {"id": uuid.uuid4().hex, "created_at": utc_now(), "status": "queued",
                   "episode_id": episode_id, "mode": mode, "model": model,
                   "predict": predict, "think": think, "payload": payload}
            self.store.insert(run)
            self.pool.submit(self._execute, run)
        return self.store.get(run["id"])

    def rerun(self, run_id: str, *, verify_model: bool = True):
        original = self.store.get(run_id)
        if original is None:
            raise KeyError("Run not found")
        return self.submit(episode_id=original["episode_id"], mode=original["mode"],
                           model=original["model"], predict=original["predict"],
                           think=original["think"], verify_model=verify_model)

    def _execute(self, run):
        run_id = run["id"]
        episode = self.episodes[run["episode_id"]]
        self.store.update(run_id, "running")
        started = time.perf_counter()
        try:
            response = self.transport(run["payload"])
            if not isinstance(response, dict) or not isinstance(response.get("message"), dict):
                raise ValueError("Model response has no message")
            raw = response["message"].get("content", "")
            raw = raw if isinstance(raw, str) else ""
            extraction = (inspect_model_output(episode, raw)
                          if run["mode"] == "抽出：現行" else Extraction(episode["id"], (), ()))
            report = analyze_reply(episode, response, extraction,
                                   expect_json=run["mode"].startswith("抽出："))
            report["check"] = judge_response(run["mode"], episode, raw)
            report["elapsed_seconds"] = round(time.perf_counter()-started, 3)
            # Final answers are useful for later comparison; never persist thinking.
            # If content starts with a think tag, suppress it entirely.
            report["content"] = (raw[:100000] if not report["content_begins_think_tag"]
                                 else "[推論タグを検出したため非保存]")
            report["full_content_truncated"] = len(raw) > 100000
            report["mode"] = run["mode"]
            report["request_payload"] = run["payload"]
            report["settings"] = {"num_predict": run["predict"], "think": run["think"]}
            # The test predicate is narrow: semantic correctness is NOT inferred
            # from well-formed JSON, nor from a normal Ollama done_reason alone.
            bad = ("なし", "不一致", "不正", "不合格", "JSON不正", "スキーマ不一致")
            failed = (response.get("done_reason") != "stop"
                      or any(w in report["check"] for w in bad))
            self.store.update(run_id, "check_failed" if failed else "completed",
                              result=report)
        except TimeoutError as exc:
            self.store.update(run_id, "timeout", error=str(exc)[:500])
        except (URLError, ConnectionError, HTTPError) as exc:
            self.store.update(run_id, "connection_error", error=str(exc)[:500])
        except Exception as exc:
            self.store.update(run_id, "error", error=f"{type(exc).__name__}: {exc}"[:500])

    def close(self):
        self.pool.close(wait=True)
