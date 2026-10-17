# Vue日常UI — RITSUKO Task読取と明示的な操作プレビュー

2026-10-17 更新。Vue 3 / TypeScript / Vite + FastAPIを使い、秘書AIの画面と実行機能を分離していく途中の実装です。通常8097は読取専用を維持し、別ポート8099でのみ明示的に実Taskコマンドを注入できます。

## 2026-10-17 時点の現在のソース構造

Web APIはVueアプリから独立した `interfaces/api/ritsuko_tasks.py` の `create_ritsuko_task_router` が正本です。Task一覧・詳細・Start・Answer・Review・Stop・OperationはどのFastAPI Hostからも同じTask Query／Commandを注入して利用できます。Vue Host `interfaces/vue_daily/app.py` はこの共通Routerを組み込み、画面配信・localhost/Origin gate・既定read-onlyを担当します。**Router単体にHost認証・通信制限はないため、直接外部公開しません**。

MAGIの判断方式は `ritsuko/magi/judgment_strategies.py` の従来段階判断／Goal判断の二つの提案Strategyとして分離し、`ritsuko/core/judgment_application.py` が提案を共通の状態判断へ適用します。新通常経路は `core/task_execution.py` → `magi/session_workflow.py` であり、旧 `async_execution.py` はProvider通信と過去の呼出元向け互換のみです。Task／Source／Action／Result／状態投影は両方式で共通です。登録前に記憶候補文・本人根拠回答を表示するVue Reviewと、MAGI席別ライブ進捗も隔離コードへ反映済みです。

根拠は[共通Router＋実Chromium＋API CI](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38997849390)と[MAGI判断Adapter回帰](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38997635970)のPASS。**実MAGI・実PGの同一Task完遂、Vueの全機能同等、本人サブPC受入は未確認**。以下に残るB-1～B-3等の節は変更当時の検証履歴であり、現行正本はこの節と`docs/SOURCE_MAP.md`・設計側統合計画を参照してください。

## 2026-10-17 Worker所在不明時のVueの操作と照会

- Runtime `main@f9349f4` で `useTasks.ts` が確定Task `running` と、現在のAPIプロセスに残る一時Operationを区別。DB上は実行中でも `operation_not_in_process` ＋ `execution_reconciliation_required` の場合、3秒周期のDB照会を続けず待機時と同じ45秒周期にする。HTTP切断時も同じ省力化を使う。Workerの生存・停止・完了を推測しない。
- `TaskCommandPanel.vue` がWorker照合待ちを示し、「保存済みTaskを再取得」で既存の読取APIを手動照会できる。**Taskの再実行・Worker再起動・DB状態変更は行わない。** 停止は既存の本人指示・永続Stop経路を維持。
- [Vue API/Chromium隔離CI](https://github.com/marshall2501/local-secretary-runtime/actions/runs/39003028095) **PASS（Chromium 11件）**、[PostgreSQL foundation](https://github.com/marshall2501/local-secretary-runtime/actions/runs/39003028033) PASS。ChromiumはAPI再起動後に似た「実行中TaskだがOperationなし」を架空応答で作り、7秒間の追加GETゼロと本人クリックによる再読取を検証。**実API process kill/restartや実Worker復旧は未検証。**

## 2026-10-17 停止済みTaskの限定再開と手動再取得

- 新共通RITSUKOの `POST /api/v1/ritsuko/tasks/{task_id}/resume` をVueの「同じTaskを再開」へ接続。対象は**本人が停止し、Source/Actionの実行・本人回答/Reviewがなく、前回Session未保存のTask**だけ。既存PostgreSQLが `can_resume` を判定し、UIはその判定だけでボタン表示する。
- 再開は同じUUIDのTaskを `paused→pending→running` に遷移し、受付時のMAGI席・判断方式・Policyを再利用。重複再開と古い試行IDのSource書込みは拒否。**Action実行済みTaskを初めから再実行する機能ではない**。
- 同Revisionで詳細を省略する通常のPollingとは別に、本人の「保存済みTaskを再取得」は詳細の取得を強制。Poll中に押した場合も再取得要求を保持。
- [実PostgreSQL隔離CI](https://github.com/marshall2501/local-secretary-runtime/actions/runs/39004381580) **PASS**、[Vue／共通API／Chromium CI](https://github.com/marshall2501/local-secretary-runtime/actions/runs/39004381575) **12件PASS**。偽MAGIと使い捨てDB／ブラウザfixtureでの受入であり、実MAGI・本人サブPC・実API process kill/restart・Action後の途中復元は未受入。運用DB・既存サービスは変更なし。

## 状態・安全境界

- 実際のTaskを**既存の`CoreTaskQueryService`とPostgreSQLの正本**から取得します。架空Task専用 `interfaces/vue_lab`（8098）とは別です。
- **通常8097は読取専用**（POSTは503）。**実Task用8099は本人が明示起動した場合のみ**日常MAGI/Source/DBへ接続し、Task受付・本人回答・Stop・回答のみReviewが可能です。NiceGUIの全機能は未移行なので既存画面も利用できます。
- 8097／8099ともに `127.0.0.1` bind。通常の画面GETは別サイトからリンクしても表示できます。Task変更POSTだけ別サイトOrigin/Sec-Fetch-Siteを拒否します。Host・Forwarded制限/no-storeは継続。インターネット・LANへ公開しません。
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
- **B-1終了時点ではB-2未実装**だった。後続のB-2で、NiceGUI側の設定・Policy Snapshot、MAGIモデル、Web/PKB/Financeの既存SourceとTask DB Portを共通Composition Rootで接続済み。ただし8097は引き続きread-only。
- **B-1終了時点ではB-3未実装**だった。後続のB-3cでVue操作UIと8099 previewは接続済み。ただし本人サブPCで実MAGIを使う受付→回答→Review→停止、API再起動時の実行継続は引き続き受入未了。

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


## 2026-10-17 Stage B-3c — Vue実Task操作パネルと明示起動

- Vue TaskCommandPanel.vue は、既存Task API経由の依頼開始（判断方式を明示選択）、本人回答、回答のみReview、Stop要求を提供する。Task状態の確定・権限・MAGI判断・Action記録は共有RITSUKO Applicationが担当し、VueはDB確定状態を再取得する。HTTP 202は受付であってTask completedの証明ではない。
- 依頼入力欄は現在Taskより上に常時配置。healthのmode=commands_enabledとなる8099でのみ編集・送信可能。8097はread_onlyとして入力を無効にし、8099へのリンクを案内する。
- クライアントはUUIDを先に生成し、レスポンス消失時の同一UUID再送に備える。未確認送信のID・依頼本文・判断方式をタブ単位sessionStorageに保持し、受付成功後に消す。ブラウザSession領域には本人原文が一時保持される。別送信先を追加していない。
- Operationは一時情報、Task TraceはDB確定情報。再起動後のrunningはWorker稼働の証明ではない。Stop要求も停止完了と区別して表示する。
- **明示的な実Task操作プレビュー**: interfaces/vue_daily/commands_preview.py は --enable-task-commands を指定した時のみ localhostの127.0.0.1:8099 で起動し、build_daily_magi_task_commands() によって既存日常DBとMAGI/Sourceを使用する。このモードは**本番Task DB書込と実LLM呼出しが可能**なので、本人による実際の操作の許可後のみ利用する。通常8097・NiceGUI8093・API8010は変更しない。
- 8099起動時は recover_pending_at_startup=False にして、過去に受付済みのpendingを勝手に起動しない。実API停止後のrunningや外部Actionを自動再実行しない。
- 本人が実DB書込を許可した後の実行例（リポジトリ直下のPowerShell）: & ".\.venv\Scripts\python.exe" -m interfaces.vue_daily.commands_preview --enable-task-commands 。画面は http://127.0.0.1:8099/ui/ 。変更したVueソースはサブPCのWSL内で npm run build してdistを再生成する必要がある。
- CIではVue build、APIのread_only/commands_enabled切替、既存HTTP POST契約、古いpendingの起動なし、明示引数なしの実Task preview起動拒否を検証する。**実ブラウザからの実Task完走、実LLM、本人回答・Review、プロセス再起動後の動作はまだ受入していない**。

## 2026-10-17 403の実機再現／修正と検証上の教訓

**本人実機の事象**: 8097のリンクから8099へ開くと、通常の画面GETが403 / cross_site_denied。画面表示ができずTaskも入力できない。原因は実Task POST用に追加したSec-Fetch-Siteの制限を、画面GETにも一律適用したこと。ブラウザが別ページからリンクを開くとcross-site/same-siteになるのは正常動作。

**なぜ入ったか・なぜ防げなかったか**: 書き込み用の条件を、本人の『開発中は不要なアクセス制限を増やさない』方針より優先して機械的に広げた。事前テストは個々のHTTP応答とJavaScript buildを見ただけで、既存画面のリンク→8099 GET→Vue入力→同一Origin POSTという**実利用経路**を試していなかった。

**変更**: GET/HEADのOriginとSec-Fetch-Siteによる画面遷移拒否を廃止。POSTの別Origin書込拒否は維持。Task Application、永続DB、MAGI、既存サービス設定は変更しない。

**再現／検証**: 新しいVueNavigationBrowserHeaderTestsで他サイトからのリンク、8097→8099遷移、URL直接入力、F5、JS取得、health、同一Origin POST202、他Origin POST403をfake Commandsで通す。修正前 [RED（403再現）](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38980440793) → 修正後 [GREEN](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38980549429)。型チェックとその他API回帰もPASS。**本人Windows実ブラウザはまだ修正後未受入**。

**既存NiceGUIとの機能差分**: VueのTask受付・追加回答・回答のみReview・Stop・保存済みTask表示は既存Applicationに接続したが、MAGI各席の設定・進捗、記憶反映Review、詳細Sourceカード、セッション表示等は現時点で未同等。全面作り直しより、まず入口から実MAGIまで1件を実証し、不足を埋める。最低限の実機受入は画面GET→Vue入力→Task受付/DB ID→MAGI結果→回答/Review→停止/F5復元。CI PASSと実機完了は混同しない。

## 2026-10-17 設計照合・実ブラウザ隔離受入（Stage C基本操作、正式切替は未受入）

承認済みVue移行計画と、現在のNiceGUI `interfaces/web/pages/core.py`、共有Task Application/Commands、PostgreSQL Query、Vue実装を照合し、以下を修正しました。

- `CurrentTask.vue` はGoal `unresolved` / `proposed` / `confirmed` を分け、達成条件・版を表示。Task確定とMAGI候補を同一視しない。
- `MagiProgress.vue` は保存済みの最新Turnから、MAGI最大3席の結果・Provider/Model/経過秒数を表示。**ライブ進捗イベントはまだ取得していない**ので、表示上もその旨明記。NiceGUIの現在Turn席別リアルタイム進捗と同等ではない。
- `TaskActivity.vue` はAction/ResultとSource URL・検証経路/時刻・監査イベントを展開。`CurrentTask.vue` は本人の操作で最新結果および全Task Traceをクリップボードにコピーできる。根拠の存在だけで回答の正しさを断定しない。
- `TaskSidebar.vue` は10件から追加読み込み（次ページの有無を1件先読みして末尾では非表示）と既存 `/core/history` への導線を追加。`useTasks.ts` はTask APIの50件上限を守るoffset query、3秒ごとのモード再照会、切断時の誤った送信可能表示の解除。
- 8097→8099の普通のGETナビゲーションを妨げた `cross_site_denied` は、GET/HEADに対する不適切なOrigin/Sec-Fetch-Siteチェックを取り除いた。Task変更POSTのみ別Origin拒否を継続。
- **Chromiumの本物のブラウザ操作**: `interfaces/vue_daily/browser_fixture.py` の架空Task APIを8097/8099で起動し、`frontend/e2e/ritsuko-flow.spec.ts` で『8097 read-only→リンクで8099を開く→実際の入力欄を編集→送信』『Task本人回答→Review→完了→F5同ID復元』『停止→Source/監査を確認』『一覧10件→追加読み込み→Goal確定表示』を自動実行。CIの[Vue/API/Chromiumブラウザ4ケース PASS（Taskコピー・追加読み込み終端も含む）](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38983961468)、[PostgreSQL回帰PASS](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38983961527)、[行末PASS](https://github.com/marshall2501/local-secretary-runtime/actions/runs/38983961490)。

開発環境で同じテストを再実行するには、サブPC既存8097/8099と**ポート競合しない完全隔離環境**で以下を使います（GitHub CIが正規受入環境）。

```bash
cd interfaces/vue_daily/frontend
npm install --no-audit --no-fund
npx playwright install chromium
npm run build
npm run test:browser
```

**残る機能差分**: NiceGUIのMAGI席別**ライブ進捗**／MAGI設定編集、記憶への反映を含むReview、詳細なObservation・Sourceカードや他日常画面はVue未移行です。**実日常DBと実MAGIを使ったWindowsサブPCでのTask縦断も未受入**。以上が成立する前にVueをNiceGUIの完全代替とみなさず、8093を維持します。
