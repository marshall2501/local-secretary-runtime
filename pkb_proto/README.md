# `pkb_proto` — legacy compatibility / isolated prototype area

`pkb_proto` は初期PKBプロトタイプ由来の互換・隔離試験領域です。日常用Web UI、RITSUKO、PKB、Capabilityの本体実装は新しい責務別packageへ移行中で、このディレクトリを新規実装の所有先にはしません。

このREADMEは現在使う入口と安全境界だけを示します。機能の実証済み範囲や現在の優先作業は、設計repoの [`STATUS`](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/STATUS.md) / [`PLAN`](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/PLAN.md) を正本としてください。

## 主な領域

- `daily_pkb.py` — `interfaces.web.app` への互換alias。日常用ポータル本体は `interfaces/web/`。
- `launch_daily_pkb.ps1` — 日常用ポータルの安全ランチャー。現在は隔離DB名、専用writer、migration、localhost bindingを検査してから起動する。
- `web_workbench.py` / `web_workbench_core.py` — 開発用Workbench。日常用UIとは分離する。
- `magi_async.py` — state-driven MAGI通信、provider adapter、turn処理。
- `magi_core_bridge.py` / `magi_task_store.py` — RITSUKO側のObservation、Task永続化、resume/review/finalization境界。
- `memory_intake.py` / `memory_extractor.py` / `memory_grounding.py` / `ingestion_gate.py` — Memory Intakeの抽出・grounding・決定的な書込み判断。
- `pending_service.py` — 自動登録できない例外候補のPending保存・確認。
- `query_service.py` / `correction_service.py` / `entity_model_service.py` — SQL-first検索、訂正、Entity/状態モデル。
- `web_research.py` — Web Research実装。
- `finance_*` — 家計・資産の取込・参照・集計スライス。

## 起動入口

日常用ポータル:

```powershell
cd D:\AI\projects\local-secretary-runtime
.\pkb_proto\launch_daily_pkb.ps1
```

開発Workbench:

```powershell
.\Launch-PKB-Web.cmd
```

ポートや依存関係を含む最新の実動作はコードとランチャーを正本とし、過去の実験メモを起動手順として使わないでください。

## 隔離DBの安全境界

`pkb_proto/sql` と `run_pending_setup.ps1` 等は、PKBの隔離試験DB向けです。現在のランチャーとサービスには `secretary_pkb_proto_20260927` および専用writerを要求する防護があります。

- `pkb_proto/sql` のmigrationを運用 `secretary` DBへ適用しない。
- 隔離試験用writerを運用DBの正本writerへ流用しない。
- fixtureや隔離試験の成功を、運用DB・実データ・統合秘書AIの成功として扱わない。
- 実データ・運用DBへ進む場合は設計repo D-08のバックアップ、復元、権限、承認条件を適用する。

## Memory Intake / Pending

LLMは候補を提案しますが、候補そのものに正本書込み権限はありません。grounding、入力種別、機密性、対象、predicate、modality、競合などをプログラム側で検査し、条件を満たす明確な低リスク本人申告はMemory Intakeから記録できます。曖昧さ・競合・対象不明・高影響などはPendingへ送ります。

Pendingは通常経路ではなく例外確認経路です。日常UIは `pending_service.py` を通して確認待ち・承認可能条件・要修正・却下・処理履歴を扱います。

## RITSUKO / MAGI

RITSUKOがTask、権限、Observation、Action/Result、停止・再開、完了条件を保持します。MAGIはユーザー原文と許可されたContextから意味理解・分析・提案を行います。MAGIの出力だけでTask完了やMemory確定を行いません。

privateなPKB ObservationをクラウドLLMへ送るかどうかは別のContext Gateで管理します。現行設計・実装の採用状況は `ARCHITECTURE` / `STATUS` を確認してください。

## 過去の実験コード

`smoke_*`、`run_*_smoke.ps1`、旧診断GUI、fixture等の一部は回帰・再現用に残っています。ファイル名や当時のコメントにある「次の作業」「未実装」を現行Roadmapとして解釈しないでください。削除可否は依存関係と再利用価値を確認して別作業で判断します。
