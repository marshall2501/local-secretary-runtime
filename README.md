# Local Secretary Runtime

Personal Local Secretary AI の実装リポジトリ。目標は **記憶・調査・計画・安全な実行・検証・記録・継続フォローアップ** を一つの依頼で完了できる秘書AIです。

[設計repoの新・目標構成図](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/assets/secretary-core-vnext.png) · [Secretary Core vNext](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/01_Architecture/SecretaryCore-vNext.md) · [最小プロトタイプ計画](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/03_Workflows/PrototypeVerticalSlice.md)

## 現状（実装済みと目標を区別）

現在：`doctor.ps1`、環境テンプレート、PC別プロファイル。今回の変更で非破壊の `setup-runtime.ps1` を追加。PostgreSQL、Secretary Core、MCP Gateway、Ollama/n8n連携は今後の実装です。

目標構成：
- **Python Secretary Core**：対話、Planner、永続Task Manager、Memory Engine、Research Engine、Tool Executor、Policy & Approval、Model Router。
- **正本**：PostgreSQL＋Source原本。SQL/全文検索を優先し、必要に応じてベクトル検索。
- **連携**：MCP、PC Agent、各種API。n8nは定期処理・Webhook・通知。
- **推論**：ローカルLLM（Ollama等）＋必要に応じたクラウドLLM。
- **承認**：AIの未確定な記憶更新はPending Claimsへ。外部操作の実行承認は独立して管理。

## セットアップ（Windows / PowerShell）

先に `.env.example` のパスを確認してください。デフォルトは `D:\AI` です。初回の安全確認：

```powershell
cd D:\AI\projects\local-secretary-runtime
.\scripts\setup\setup-runtime.ps1 -WhatIf
.\scripts\setup\setup-runtime.ps1
.\scripts\doctor\doctor.ps1
```

`setup-runtime.ps1` は不足フォルダだけを作り、`.env` がない場合のみ `.env.example` からコピーします。既存ファイルを上書きしません。Git/Docker/Pythonの有無を調べ、Node.js/OllamaがなければTODOとして表示します。ソフトウェアの自動インストール、Docker起動、外部repoの変更はしません。`-RunDoctor` を指定するとセットアップ後に既存doctorを起動します。Windows実機では未検証なので、最初に `-WhatIf` を確認してください。

`doctor.ps1` は現在固定の `D:\AI` パスを使用しています。サブPCなど別パスでセットアップする場合は、doctorの可変パス対応が必要です。

## セキュリティと別プロジェクト保護

`.env`、APIキー、実データ、DB本体、Docker volume、モデル、ログ、バックアップをGitへ入れないでください。`yt-topic-search` は同じPC上で稼働する**独立した別プロジェクト**です。構築時に他のCompose project、コンテナ、volume、portを変更しません。

## 次の実装

専用Docker Compose・PostgreSQLとschema migration・Source Archive、永続Task ManagerとSecretary Core最小APIを追加し、記憶→調査→計画→承認→安全な実行→検証→記録→フォローアップを通したテストを行います。
