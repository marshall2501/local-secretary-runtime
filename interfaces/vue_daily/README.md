# Vue日常UI — 保存済みRITSUKO Task読取プレビュー

2026-10-16 時点。Vue 3 / TypeScript / Vite + FastAPIを使い、秘書AIの画面と実行機能を分離していく途中の実装です。

## 状態・安全境界

- 実際のTaskを**既存の`CoreTaskQueryService`とPostgreSQLの正本**から取得します。架空Task専用 `interfaces/vue_lab`（8098）とは別です。
- 現時点のVue日常画面は**読取専用**。Task開始・回答・停止・Reviewの**API契約はStage B-1で追加**したものの、実運用のMAGI/Source/DBに接続するComposition Rootは未接続で、通常8097のPOSTは**503**を返します。記憶登録・外部Actionは公開しません。NiceGUI日常画面は引き続き利用可能です。
- 明示起動時だけ `127.0.0.1:8097` で動作。Host / Origin / Sec-Fetch-Site / Forwarded制限、レスポンスno-store。インターネット・LAN公開、第三者向けの認証設計は未実装のため、そのような構成で公開しません。
- Production DBに接続するときは既存の `bootstrap.web_runtime` → `PostgresCoreTaskQueryRepository` → daily runtime Secretを**Python側だけ**で使います。Browser/TypeScript/生成JSにDB資格情報は入りません。Secretの値をログへ表示しません。
- ブラウザF5ではTask IDをURLハッシュから復元し再照会します。**実行中Taskの無停止継続・API再起動後のTask実行再開**は本画面の実績ではありません。

## WindowsサブPCでの明示起動（既存GUIを変更しない）

リポジトリを最新 `main` に更新した後、WSL内でVueの依存をインストールしてビルドします。

```powershell
wsl -e bash -ic 'cd /mnt/d/AI/projects/local-secretary-runtime/interfaces/vue_daily/frontend && npm install --no-audit --no-fund && npm run build'
```

次にPowerShellまたはExplorerから、リポジトリ直下の `Launch-Local-Secretary-AI-Vue-Preview.cmd` を**明示的に実行**します。`http://127.0.0.1:8097/ui/` をブラウザで開きます。

- 起動スクリプトは既存の`.env.postgres`と`secrets/secretary-daily-runtime-password.txt`から、Runtimeがすでに利用する接続条件を参照します。生成物やgitにはSecretを保存しません。
- 8097が使用中なら起動に失敗します。既存NiceGUI（8093）、Secretary API（8010）、PVA（8015）、架空Vue（8098）は停止・変更しません。
- プレビューの停止は起動したPowerShellで`Ctrl+C`。これは新規プレビューのみを終了し、通常のNiceGUIやDBを止めません。

## テスト

```powershell
& ".\.venv\Scripts\python.exe" -m unittest discover -s tests -p test_task_composition.py -v
& ".\.venv\Scripts\python.exe" -m unittest discover -s tests -p test_vue_daily_api.py -v
```

CIはテスト用の注入Queryを使うため、実DB/LLM/外部Actionに接続しません。GitHub CI PASSとWindows実ブラウザでの本人受入は別です。

## 今後

実Task開始・再開・停止・ReviewのAPIは、既存NiceGUI画面内に残るRunner/状態/PortをTransport非依存のApplication・実行管理へ移し、DB Task/Policyの不変条件を共有してから追加します。Vueに独立のTaskエンジンやDBマスタを作りません。

## 2026-10-16 Stage A — NiceGUIを基準にしたVueの土台（サブPC実機受入済み）

- `frontend/src/App.vue` は薄いroot。`app/SecretaryLayout.vue` が共通上部ナビ、`features/ritsuko/RitsukoPage.vue` がRITSUKOの中央領域と**右側Task一覧**を合成する。
- `features/ritsuko/components/` の `CurrentTask`、`TaskSidebar`、`TaskActivity` は機能別表示に限定。`useTasks.ts` がTask選択・URLハッシュからの復元・3秒ごとのAPI再照会・接続失敗表示を管理。`types.ts` はRITSUKO確定TaskとMAGI候補を区別し、`api.ts` はTask照会だけを定義する。
- `shared/api/http.ts` は共通HTTP read adapter、`shared/navigation.ts` は共通ナビ契約、`shared/styles/base.css` は画面枠と狭い画面向け表示。新規の業務ルールやTask永続状態はVueへ移していない。
- **未移行のTOP／PKB／家計／設定等**は上部ナビから既存NiceGUI `127.0.0.1:8093` の各画面へ移動できる。空のVue画面や動作しない操作ボタンを完成機能として設置しない。
- 実Task読取API（8097）と既存NiceGUI（8093）のソース・通常ランチャー・本番Task DBは変更しない。新規依頼・本人回答・停止はNiceGUIで行い、Vue側は読取専用のまま。
- 以前の `/ui/#task=<UUID>` を維持し、URLハッシュを使ったF5復元、Task選択、ブラウザ戻る・進むを考慮する。**CIと実機受入は別**。Stage AはサブPC画面で上部ナビ・右Task一覧18件・同Task ID/rev=2の結果再取得を確認済み。実Taskの更新操作やMAGI進捗のVue側受入はまだ。

## 2026-10-16 Stage B-1 — 実Task操作の受付契約（実接続は未完了）

- `ritsuko/application/task_commands.py` の `TaskCommands` は、Task開始・本人回答・回答のみProposal Reviewを既存 `StateDrivenTaskApplication` へ渡し、協調停止Event、同一Task多重受付の回避、プロセス内Operation進捗を管理する。**Operationで`execution_finished`が返っても、DBのTaskが`completed`とは限らない**。
- FastAPI `create_app(queries=..., commands=...)` のように正しい実Task依存が注入された場合だけ、`POST /api/v1/ritsuko/tasks`、`/{id}/answer`、`/{id}/stop`、`/{id}/review`、`GET /api/v1/ritsuko/operations/{id}` が動作する。**既定の `create_app(queries=...)` と起動コマンドは変更せず、POSTは503**。HTTP 202は受付のみで、Task保存が直後に完了した保証ではない。
- `review` の `remember` は未対応。Memory Intakeを別経路で正しく確定する接続が必要。MAGI実行の停止も次の境界で反映する協調停止であり、即時停止の保証ではない。
- **B-2未実装**: NiceGUI側の設定・権限Snapshot、MAGIモデル、Web/PKB/Financeの既存Source実行、Task DB Portを、GUI非依存のComposition Rootで一式bindする。これができるまで、8097を実Task write入口として有効化しない。
- **B-3未実装**: 実DBに対するTask受付・回答・停止・ReviewのサブPC実機確認、障害時のDB状態、Vueの実操作画面接続。APIプロセス再起動時の実行継続は確認・保証していない。 

## 2026-10-16 Stage B-2 — 画面非依存の実MAGI/Source/DB組立（隔離検証まで）

- `ritsuko/application/pkb_observation.py` は旧NiceGUIの対象PC/部品解決と公開可能な技術識別子抽出を共通化し、`ritsuko/application/source_execution.py` は権限付きWeb調査／家計読取を共通化する。旧NiceGUIの `_execute_magi_pkb_request`／`_execute_magi_source_request` はこの共通処理へ委譲する。
- `bootstrap/magi_task_runtime.py` の `MagiTaskBindings`／`build_magi_task_commands` は、既存 `StateDrivenTaskApplication` と Task/Action/Result Repository、MAGI席設定、Task開始時のPolicy Snapshot、PKB/Web/家計のSourceを画面と独立して結合する。`build_daily_magi_task_commands()` は日常Runtimeの既存DB・Service Connection経路を使うが、**通常8097では呼び出していない**。
- `tests/test_magi_task_runtime_composition.py` はfake DBとfake MAGIを使い、Task開始→本人回答→回答のみReviewとPolicy Snapshotの再利用を検査。`tests/test_magi_shared_source.py` は曖昧なPKB対象で広い検索に拡張しないこと、権限のないWeb検索を実行しないこと、Web fallback時にも同じ公開検索文を使うことを確認。既存NiceGUIのPKB/Web回帰も維持する。
- **未受入**: 本人のサブPC実DBと実MAGI/Source接続での受付・回答・停止・Review、MAGI設定/権限Snapshotの実機照合、Goal判断方式の画面選択同等性、JobのAPI process再起動後復旧。これらを確認するまで通常8097から実Taskのwriteを有効化しない。Vue画面の操作UIも未接続。
