# Vue日常UI — 保存済みRITSUKO Task読取プレビュー

2026-10-15 時点。Vue 3 / TypeScript / Vite + FastAPIを使い、秘書AIの画面と実行機能を分離していく途中の実装です。

## 状態・安全境界

- 実際のTaskを**既存の`CoreTaskQueryService`とPostgreSQLの正本**から取得します。架空Task専用 `interfaces/vue_lab`（8098）とは別です。
- 現時点のVue日常画面は**読取専用**。Task開始、MAGI呼出し、本人回答、停止、Proposal Review、記憶登録、外部Actionは**実装していません**。NiceGUI日常画面は引き続き利用可能です。
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
