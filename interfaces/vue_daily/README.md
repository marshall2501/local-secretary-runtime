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

## Stage B-2 日常DB設定の読取プローブ

サブPCの実機で日常DB・MAGI設定・LAN Policy・Task一覧を確認するための再利用可能なコマンド：

```powershell
& "D:\AI\projects\local-secretary-runtime\.venv\Scripts\python.exe" "D:\AI\projects\local-secretary-runtime\scripts\db\probe_magi_runtime_readonly.py"
```

- 既存Vue Launcherと同じ `.env.postgres` のPortと `secrets/secretary-daily-runtime-password.txt` の**ファイルパス**を検証プロセス内だけに設定する。親PowerShellの環境変数・Secretファイル・DB設定を変更しない。
- 有効MAGIの総数と3席の `ENABLED`／`DISABLED`／`NOT_ASSIGNED`、LAN Policyの有無、Task一覧の読取可否だけを出す。Secret値や実Task本文を表示しない。Task書込・MAGI/Source実行を行わない。
- このスクリプトは**実DBの設定読取のみ**。Model endpoint・接続資格情報の有効性やTask作成/回答/Review/Stopの受入ではない。通常Vue 8097は依然 `read_only`、POSTは503。
- 初回の実機読取は本人PowerShellの一時プローブで `Composition: OK` / `MAGI active: 1` / `LAN policy: SET` / `Task DB read: OK` を確認済み。**この新しいファイルの実機実行は未実施**、fake隔離CIで検証済み。3席のうち1席有効が意図どおりかは別確認。

## 2026-10-16 P1 Goal判断の既存Task経路への統合 — 第1段階

- `POST /api/v1/ritsuko/tasks` のリクエストは、`request`に加えて`decision_strategy: "standard" | "llm"`の**明示指定を必須**とする。省略・未知の文字列は422。既存NiceGUIの初期判断方式やローカルUI PreferenceとAPI既定が食い違うのを避けるため、ここで暗黙の方式を選ばない。**通常8097は今もread_onlyでPOST 503**（正しいBodyの場合）。
- `TaskCommands.start`は有効MAGI席設定をスナップショットし、既存Applicationの `dialogue_starter` Portから選択方式を共通Runnerへ渡す。`bootstrap/magi_task_runtime.py`のRunnerは指定されたStarterを利用し、**2個目のGoal実行ループは作らない**。
- 現行MAGIのLLM判断は試験上有効1席限定。2席以上を選択してもDB割当を自動変更せず409として拒否。1席・3席の自由な選択とLLM判断の組合せはP1の後続改修で扱う。
- `tests/test_magi_task_runtime_composition.py`で判断方式・同一Task ID・保存・本人回答とReview後の維持、`tests/test_vue_task_commands.py`で無断席数変更拒否、`tests/test_vue_daily_api.py`で方式必須／未知形式422・HTTP配送を隔離確認。Task状態や根拠評価を2系統へ複製しない設計の最初の接続修正に相当する。
- **未完了**: `goal_loop.py`のWeb限定Schema・Result判定、`magi_bridge.py`のGoal専用Task投影、`observation_loop.py`のGoal専用Result処理、API再起動後のJob/Stop・復旧、実MAGIの多席Goal評価。これらを受入済みとはしない。既存8093・8097の稼働切替や設定変更はしない。

## 2026-10-16 P1 Goal判断の情報源・Task確定の共通化（隔離テスト）

- `ritsuko/magi/goal_contract.py` が `AUTO_READ_SOURCES` と同じ `pkb/web/finance` をGoal判断の `request_read` で受け、`goal_loop.py` は選択されたSourceをRITSUKOへ渡す。Taskの取得可否は従来Resource Catalog/権限のもとで判定し、任意Tool・外部Actionを開放しない。
- `goal_requires_source` と `goal_requires_web` を区別し、「私の家計」等をWeb検索必須とみなさない。明示根拠を要するGoalを未読の `not_required` で即時完了させない。表層語句による暫定Gateであり、**意味的Goal評価や情報源選択をPythonで完全解決したという主張ではない**。
- `goal_loop.py::goal_task_projection` は保存済み旧Task用の互換関数とし、確定状態の正本は `ritsuko/core/magi_bridge.py::task_projection` に統合。根拠IDはRITSUKO Task内の検証済み `pkb/web/finance` 観測のみ認める。未実行・読取失敗・他TaskのID申告だけで完了としない。Web検索Providerの引用報告と本文確認も区別する。
- `tests/test_goal_source_neutral.py` はfake Task/LLM/Policy/Sourceで、Source選択、同じTask IDのread→記録→評価→完了、未取得・偽Resultでの拒否を検証。既存 `test_goal_loop.py` および `test_magi_core_bridge.py` とともにCIへ追加。
- **未受入**: 実モデルでのPKB/Finance Goal達成、1〜3席のLLM判断実装、操作/Operationの永続化と再起動復旧、本人用Vue Task操作、複数Sourceや外部Actionによる実際のGoal完走。通常8097はread-only/POST503。

## Stage B-3a: 保存済みTaskのOperation照会フォールバック

- `TaskCommands.operation(task_id)` は実行中／直近完了Jobのメモリ記録があれば従来のOperationを返す。メモリから消えていても、既存Query Serviceの`trace(task_id)`で**保存済みTaskの状態だけ**を確認し、404にはしない。
- DB fallback応答では `status: operation_not_in_process`、`source: persisted_task`、`task_status` を返す。DB Taskが `running` の場合は `execution_reconciliation_required: true`。これは**TaskがDBに存在すること**であり、Worker稼働・再開可能・処理成功の保証ではない。停止要求も不明なので`stop_requested: null`とする。Task本文・MAGI Session等はレスポンスへ返さない。
- もとのTaskがDBに存在しない（202後DB作成前にAPI停止など）場合は引き続き404。HTTP 202はなお**プロセス内受付のみ**。永続受付・Worker/PC再起動後の安全な再実行は未実装。
- `tests/test_vue_task_commands.py` でDBに存在する `running/waiting_external/paused/completed/failed` とWorker不在・stop不可、`tests/test_vue_daily_api.py` で再生成されたAPIと404をfake Queriesで検証。
- 通常8097は実Task Command未注入の`read_only`でPOST 503。今回の変更はデフォルト8097やNiceGUI/DBの運用を変更しない。

## Stage B-3b: 永続Task受付・未着手復旧（2026-10-17）

- **実Task Commandを明示的に注入した時だけ**、`TaskCommands.start` はJob実行より先に同じPostgreSQLの `tasks.status=pending` と `checkpoint.start_intent` をatomicにcommitする。保存するものは依頼本文・選択判断方式・メンバー設定・Task固定Policy snapshotであり、Secret値・Credential原文ではない。DB保存が失敗したら202を返さず、Jobも動かさない。
- `run_observation_loop` は従来どおり一系統。`bootstrap/magi_task_runtime.py` は従来の `create_task_record` Portに `claim_start_record` を注入し、`pending→running` の `FOR UPDATE` claimが成功したJobだけMAGIへ進める。既存NiceGUIの個別Task作成経路は変更しない。
- `TaskCommands.recover_pending()` は保存済みで**未着手のpending Taskだけ**を列挙し、同じTask ID、当時の判断方式／member設定／Policyを使って再開。API `create_app(..., commands=...)` は起動時に最大20件のpending復旧を要求する。**通常8097の`create_app(queries=...)`はcommands未注入のread-onlyなので、起動時復旧やTask writeは実行しない**。DB上で既にrunningのTaskを自動再実行しない。
- StopはRepositoryの `request_stop` で永続化。pendingは`paused`へ確定してclaim不許可。runningはまず `stop_requested=true` を記録し、Workerによる停止完了は別。API再起動後は保存済みStopの有無をTaskの照会から確認でき、`running`をWorker生存と見なさない。
- HTTP `POST /api/v1/ritsuko/tasks` に省略可能な `task_id: UUID` を追加。通信途絶後に**同一UUID・依頼原文・判断方式**で再送した場合はDB `ON CONFLICT DO NOTHING`と読取照合を使い、Jobを二重作成せず `already_accepted` を返す。異なるIntentを同じUUIDに送った場合は409。UUIDなしの新規リクエストは従来どおりServerが採番するため、クライアントの完全な再送冪等性にはUI側でTask IDを再利用する対応が必要。
- **隔離検証**: fake Repositoryの受付→MAGI→回答→Review、DBコミット失敗時のJob未実行、同じUUIDの再送、API再構築後のpending復旧、pending停止後の復旧拒否、API明示bind時だけの起動処理、SQL unit回帰を追加。[Vue/API CI](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38926295720) PASS。使い捨てDocker PostgreSQLで実際に2 Worker同時claim／同じUUID再送／pending Stopを[PostgreSQL CI](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38926653819) PASS（`PASS: disposable PG durable intake, replay, 2-worker claim, stop-before-start`）。既存[NiceGUI/PKB回帰](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38926653778)・[改行](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38926653795) PASS。
- **未実証／残課題**: 本人サブPCでの新コード反映・通常実DBのMAGI/Source Task、PC/APIを実際にKill→Restartした場合のジョブ復旧と中断中runningの照合、ユーザー回答・Reviewの202以前永続化、複数pendingの継続drain、Vue操作画面がUUIDを保持して再送する導線、実MAGIの1〜3席Goal判断。現時点で新Runtimeの通常8097 write接続・既存8093起動構成・MAGI席設定／権限は変更していない。
