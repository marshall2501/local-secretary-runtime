"""Tkinter desktop workbench for LOCAL fictional PKB extraction diagnostics.

No database connection, production files, gold answers, cloud fallback or
automatic downloads. Worker uses only 127.0.0.1:11434 and bundled ten episodes.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .episode_intake import load_fixture
from .extraction_service import Extraction, inspect_model_output
from .diagnostic_cases import MODES, request_for, judge_response
from .gui_helpers import analyze_reply, export_report

OLLAMA = "http://127.0.0.1:11434"
FIXTURE = Path(__file__).parent / "fixtures" / "episodes.json"


def installed_models() -> list[str]:
    with urlopen(OLLAMA + "/api/tags", timeout=5) as response:
        result = json.load(response)
    return sorted({
        entry["name"] for entry in result.get("models", [])
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    })


def call_local_model(episode: dict, model: str, predict: int, think: str,
                     mode: str = '抽出：現行') -> dict:
    messages, json_format = request_for(episode, mode)
    payload = {
        "model": model, "stream": False,
        "messages": messages,
        "options": {"temperature": 0, "num_predict": predict},
    }
    if json_format:
        payload["format"] = "json"
    if think == "無効":
        payload["think"] = False
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        OLLAMA + "/api/chat", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=240) as response:
        result = json.load(response)
    if not isinstance(result, dict) or not isinstance(result.get("message"), dict):
        raise ValueError("Ollama response has no message object")
    return result


class Workbench:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PKB ローカル抽出検証 — 架空データ専用")
        self.root.geometry("1160x780")
        self.root.minsize(880, 620)
        self.episodes = load_fixture(FIXTURE)
        self.by_id = {ep["id"]: ep for ep in self.episodes}
        self.results: list[dict] = []
        self.events: queue.Queue = queue.Queue()
        self.running = False
        self.batch_started: float | None = None
        self.stop_requested = threading.Event()

        self.model_var = tk.StringVar(value="qwen3.5:9b")
        self.episode_var = tk.StringVar(value=self.episodes[0]["id"])
        self.predict_var = tk.StringVar(value="1100")
        self.think_var = tk.StringVar(value="自動")
        self.mode_var = tk.StringVar(value="抽出：現行")
        self.status_var = tk.StringVar(value="初期状態：DBには接続しません")
        self._build()
        self.choose_episode()
        self.mode_changed()
        self.root.after(100, self.process_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        ttk.Label(
            top, text="架空データ10件のみ／ローカルOllama／候補は確認待ち／DB書込なし",
            foreground="#12614c",
        ).pack(anchor="w")

        settings = ttk.Frame(top)
        settings.pack(fill="x", pady=(9, 7))
        ttk.Label(settings, text="モデル").pack(side="left")
        self.model_box = ttk.Combobox(
            settings, textvariable=self.model_var, width=26, state="normal"
        )
        self.model_box.pack(side="left", padx=(5, 5))
        ttk.Button(settings, text="モデル一覧を更新", command=self.refresh_models).pack(
            side="left", padx=(0, 15)
        )
        ttk.Label(settings, text="生成上限").pack(side="left")
        ttk.Combobox(
            settings, textvariable=self.predict_var, state="readonly",
            width=8, values=("1100", "2048", "4096"),
        ).pack(side="left", padx=(5, 15))
        ttk.Label(settings, text="推論モード").pack(side="left")
        ttk.Combobox(
            settings, textvariable=self.think_var, state="readonly",
            width=9, values=("自動", "無効"),
        ).pack(side="left", padx=5)

        actions = ttk.Frame(top)
        actions.pack(fill="x")
        ttk.Label(actions, text="検証").pack(side="left")
        self.mode_box = ttk.Combobox(
            actions, textvariable=self.mode_var, values=MODES,
            state="readonly", width=18
        )
        self.mode_box.pack(side="left", padx=(5, 10))
        self.mode_box.bind("<<ComboboxSelected>>", self.mode_changed)
        ttk.Label(actions, text="Episode").pack(side="left")
        self.episode_box = ttk.Combobox(
            actions, textvariable=self.episode_var,
            values=tuple(self.by_id), state="readonly", width=11
        )
        self.episode_box.pack(side="left", padx=5)
        self.episode_box.bind("<<ComboboxSelected>>", self.choose_episode)
        self.one_button = ttk.Button(actions, text="選択した1件を診断",
                                     command=lambda: self.start(False))
        self.one_button.pack(side="left", padx=(10, 5))
        self.all_button = ttk.Button(actions, text="10件をまとめて検証",
                                     command=lambda: self.start(True))
        self.all_button.pack(side="left", padx=5)
        self.stop_button = ttk.Button(actions, text="次の件から停止",
                                      command=self.request_stop, state="disabled")
        self.stop_button.pack(side="left", padx=5)
        ttk.Button(actions, text="結果をJSON保存", command=self.save_report).pack(
            side="right", padx=5
        )
        ttk.Button(actions, text="結果JSONをコピー", command=self.copy_report).pack(
            side="right", padx=5
        )
        ttk.Button(actions, text="結果をクリア", command=self.clear_results).pack(
            side="right", padx=5
        )

        divider = ttk.Panedwindow(self.root, orient="vertical")
        divider.pack(fill="both", expand=True, padx=10, pady=5)

        upper = ttk.Frame(divider)
        divider.add(upper, weight=1)
        ttk.Label(upper, text="選択中の架空Episode原文").pack(anchor="w")
        self.original = tk.Text(upper, height=5, wrap="word", state="disabled")
        self.original.pack(fill="both", expand=True, pady=(3, 7))

        lower = ttk.Frame(divider)
        divider.add(lower, weight=4)
        ttk.Label(lower, text="実行結果：項目をクリックして詳細表示").pack(anchor="w")
        columns = ("episode", "mode", "elapsed", "done", "tokens", "content", "json", "candidates", "errors")
        self.table = ttk.Treeview(
            lower, columns=columns, show="headings", height=9, selectmode="browse"
        )
        for col, title, width in (
            ("episode", "Episode", 75), ("mode", "検証項目", 115),
            ("elapsed", "実行秒", 65),
            ("done", "終了理由", 100),
            ("tokens", "生成tokens", 90), ("content", "本文文字数", 90),
            ("json", "JSON解析", 220), ("candidates", "候補", 60),
            ("errors", "拒否", 60),
        ):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, anchor="w", stretch=(col=="json"))
        self.table.pack(fill="x", pady=(3, 5))
        self.table.bind("<<TreeviewSelect>>", self.show_details)

        notebook = ttk.Notebook(lower)
        notebook.pack(fill="both", expand=True)
        self.detail = tk.Text(notebook, wrap="word", state="disabled")
        notebook.add(self.detail, text="応答・抽出候補")
        self.raw_preview = tk.Text(notebook, wrap="word", state="disabled")
        notebook.add(self.raw_preview, text="モデル本文の先頭（推論テキストは除外）")
        ttk.Label(
            self.root, textvariable=self.status_var, anchor="w", padding=8
        ).pack(fill="x")

    @staticmethod
    def replace_text(widget: tk.Text, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def choose_episode(self, _event=None):
        episode = self.by_id[self.episode_var.get()]
        self.replace_text(
            self.original,
            f"{episode['id']}　{episode['source_kind']}　"
            f"記録 {episode['recorded_at']}　発生 {episode['occurred_at']}\n"
            + episode["text"],
        )

    def mode_changed(self, _event=None):
        if not self.running:
            self.all_button.configure(
                state="normal" if self.mode_var.get() == "抽出：現行" else "disabled"
            )

    def refresh_models(self):
        if self.running:
            return
        try:
            models = installed_models()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("ローカルOllamaに接続できません",
                                 f"127.0.0.1:11434 の状態を確認してください。\n{exc}")
            return
        self.model_box.configure(values=models)
        if models and self.model_var.get() not in models:
            self.model_var.set(models[0])
        self.status_var.set(f"ローカルモデル {len(models)}件を取得")

    def start(self, all_episodes: bool):
        if self.running:
            return
        model = self.model_var.get().strip()
        if not model or any(ch.isspace() for ch in model):
            messagebox.showerror("モデル未指定", "インストール済みのモデル名を指定してください。")
            return
        try:
            models = installed_models()
            if model not in models:
                raise ValueError("モデルがインストールされていません。自動ダウンロードはしません。")
        except (OSError, ValueError) as exc:
            messagebox.showerror("ローカルモデル確認失敗", str(exc))
            return
        mode = self.mode_var.get()
        if all_episodes and mode != "抽出：現行":
            messagebox.showinfo("1件ずつ検証", "切り分けモードは1件だけ実行してください。")
            return
        ids = list(self.by_id) if all_episodes else [self.episode_var.get()]
        self.running = True
        self.batch_started = time.perf_counter()
        self.stop_requested.clear()
        self.one_button.configure(state="disabled")
        self.all_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set(f"開始：{len(ids)}件、モデル {model}。DBへの書込みなし")
        settings = (model, int(self.predict_var.get()), self.think_var.get(), mode)
        thread = threading.Thread(target=self.worker, args=(ids, settings), daemon=True)
        thread.start()

    def worker(self, ids, settings):
        model, predict, think, mode = settings
        for episode_id in ids:
            if self.stop_requested.is_set():
                break
            episode = self.by_id[episode_id]
            started = time.perf_counter()
            try:
                response = call_local_model(episode, model, predict, think, mode)
                raw = response["message"].get("content", "")
                extraction = (inspect_model_output(episode, raw)
                              if mode == "抽出：現行"
                              else Extraction(episode["id"], (), ()))
                report = analyze_reply(episode, response, extraction,
                                       expect_json=mode.startswith("抽出："))
                report["mode"] = mode
                report["check"] = judge_response(mode, episode, raw)
                report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                report["settings"] = {"num_predict": predict, "think": think}
                self.events.put(("result", report))
            except Exception as exc:
                elapsed = round(time.perf_counter() - started, 3)
                self.events.put(("error", episode_id,
                                 f"{type(exc).__name__}: {exc}", elapsed))
        self.events.put(("finished",))

    def process_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "result":
                    report = event[1]
                    report["run_index"] = len(self.results) + 1
                    self.results.append(report)
                    idx = len(self.results) - 1
                    self.table.insert("", "end", iid=str(idx), values=(
                        report["episode"], report["mode"],
                        f"{report['elapsed_seconds']:.2f}",
                        report["done_reason"],
                        report["eval_count"], report["content_length"],
                        report["json_parse"], report["candidate_count"],
                        len(report["errors"]),
                    ))
                    self.table.selection_set(str(idx))
                    self.table.see(str(idx))
                    self.show_details()
                    self.status_var.set(
                        f"取得 {len(self.results)}件／最新 {report['episode']}："
                        f"{report['check']}／{report['elapsed_seconds']:.2f}秒"
                        "（候補はすべて確認待ち）"
                    )
                elif kind == "error":
                    _, episode_id, message, elapsed = event
                    self.status_var.set(f"{episode_id}: {elapsed:.2f}秒で失敗：{message}")
                    messagebox.showerror(
                        "ローカル抽出エラー",
                        f"{episode_id}／{elapsed:.2f}秒\n{message}"
                    )
                elif kind == "finished":
                    self.running = False
                    self.one_button.configure(state="normal")
                    self.mode_changed()
                    self.stop_button.configure(state="disabled")
                    elapsed = (
                        time.perf_counter() - self.batch_started
                        if self.batch_started is not None else 0.0
                    )
                    self.batch_started = None
                    self.status_var.set(
                        f"終了：今回の実行時間 {elapsed:.2f}秒／累計{len(self.results)}件。"
                        "正解判定ではありません。DB書込なし。"
                    )
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self.process_events)

    def show_details(self, _event=None):
        selected = self.table.selection()
        if not selected:
            return
        report = self.results[int(selected[0])]
        summary = {k: v for k, v in report.items()
                   if k not in ("content_preview", "content_truncated")}
        self.replace_text(self.detail, json.dumps(summary, ensure_ascii=False,
                                                 indent=2, default=str))
        self.replace_text(
            self.raw_preview,
            report["content_preview"] +
            ("\n\n[本文は1200文字まで表示]" if report["content_truncated"] else ""),
        )

    def request_stop(self):
        self.stop_requested.set()
        self.status_var.set("停止予約：現在の1件が終了した後、残りを実行しません。")

    def clear_results(self):
        if self.running:
            return
        self.results.clear()
        for item in self.table.get_children():
            self.table.delete(item)
        self.replace_text(self.detail, "")
        self.replace_text(self.raw_preview, "")
        self.status_var.set("結果をクリアしました。DBには接続していません。")

    def report_json(self) -> str:
        return export_report(
            self.results, model=self.model_var.get(),
            settings={"note": "Per-item model settings stored with each result"},
        )

    def copy_report(self):
        if not self.results:
            messagebox.showinfo("コピーする結果なし", "先に架空Episodeを診断してください。")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.report_json())
            self.root.update_idletasks()
        except tk.TclError as exc:
            messagebox.showerror("コピー失敗", str(exc))
            return
        self.status_var.set(f"結果JSONをクリップボードにコピーしました（{len(self.results)}件）。")

    def save_report(self):
        if not self.results:
            messagebox.showinfo("保存する結果なし", "先に架空Episodeを診断してください。")
            return
        target = filedialog.asksaveasfilename(
            title="架空データの検証結果を保存",
            defaultextension=".json", filetypes=[("JSON", "*.json")],
            initialfile="pkb-fictional-extraction-report.json",
        )
        if not target:
            return
        try:
            Path(target).write_text(
                self.report_json(),
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showerror("保存失敗", str(exc))
            return
        self.status_var.set("JSONレポートを保存しました。架空データのみ。")

    def close(self):
        if self.running:
            if not messagebox.askyesno(
                "抽出実行中", "現在のHTTP要求が残っている可能性があります。画面を閉じますか？"
            ):
                return
            self.stop_requested.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    Workbench(root)
    root.mainloop()


if __name__ == "__main__":
    main()
