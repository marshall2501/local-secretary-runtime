# setup-local-secretary-docs.ps1
# Creates initial design documents for Local Secretary AI.
#
# Usage:
#   cd D:\AI\projects\local-secretary-ai
#   .\setup-local-secretary-docs.ps1
#
# To overwrite existing files:
#   .\setup-local-secretary-docs.ps1 -Force

param(
    [string]$Root = "D:\AI\projects\local-secretary-ai",
    [switch]$Force
)

function Write-Doc {
    param(
        [string]$Path,
        [string]$Content
    )

    $parent = Split-Path $Path -Parent
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Write-Host "[CREATE DIR] $parent"
    }

    if ((Test-Path $Path) -and (-not $Force)) {
        Write-Host "[SKIP] $Path"
        return
    }

    $Content | Set-Content -Path $Path -Encoding UTF8
    if ($Force) {
        Write-Host "[WRITE] $Path"
    } else {
        Write-Host "[CREATE] $Path"
    }
}

$files = @{}

$files["$Root\README.md"] = @'
# Local Secretary AI

Personal Local Secretary AI は、ユーザーに関連する全領域の情報を、正確に記録・検索・判断・回答できるようにするための個人用秘書AIプロジェクトです。

このリポジトリは、実装コードではなく **設計・仕様・判断履歴・プロンプト・調査メモ** を管理するための設計書リポジトリです。

## 目的

```text
ユーザーに関連する全領域の情報を、
対象・属性・出来事・実施内容・結果・仮説・判断・根拠として正確に扱える
Personal Local Secretary AI を構築する。
```

## 対象範囲

対象はPCに限定しません。

- PC
- ラジコン
- 携帯電話
- ゲーム
- 健康
- 買い物
- 生活
- 契約
- プロジェクト
- 調査メモ
- 日記
- 過去ログ

PCは最初の代表ユースケースであり、設計は全ドメイン対応を前提にします。

## 現在の採用方針

```text
Airtable + n8n + Local Qwen/Ollama + ChatGPT/Claude
```

- Airtable: 構造化Memory DB / 人間レビューUI
- n8n: Orchestrator / AI Router
- Local Qwen/Ollama: 一次抽出Worker
- ChatGPT/Claude: 高精度レビュー / 難しい統合 / 設計相談
- Corpus2Skill: 将来的なSkill Navigation Layer候補

## 重要原則

- LLMは記録の正本にしない
- AIは候補を作り、人間が承認する
- 事実・仮説・判断を分離する
- ツール依存ではなく設計原則を優先する
- 対象ドメインをPCに限定しない

## ドキュメント構成

```text
docs/
├─ 00_Project
├─ 01_Architecture
├─ 02_Database
├─ 03_Workflows
├─ 04_AI
├─ 05_Implementation
├─ 06_Research
├─ 07_Meetings
└─ 99_Archive
```

## 次に読むもの

1. `docs/00_Project/Vision.md`
2. `docs/00_Project/Principles.md`
3. `docs/00_Project/Requirements.md`
4. `docs/01_Architecture/Overall.md`
5. `docs/02_Database/Airtable.md`
'@

$files["$Root\docs\00_Project\Vision.md"] = @'
# Vision

## 目的

Personal Local Secretary AI は、ユーザーに関連する情報を長期的・構造的に扱うための個人用秘書AIです。

単なるチャットボット、RAG、文書検索、PCトラブル管理ツールではありません。

目指すものは以下です。

```text
ユーザーの環境・趣味・機器・検証・判断履歴を把握し、
過去の文脈を踏まえて、
記録・検索・判断・回答を支援する秘書AI。
```

## 背景

ChatGPTのメモリ機能では、以下が難しいと分かりました。

- 何を記録するかを明示的に制御できない
- PCごとの構成やトラブル履歴を構造化して保持しにくい
- スレッドをまたいだ過去情報の参照が安定しない
- 実施済み・未実施・仮説・暫定結論・未解決を分けにくい
- どの情報が正本なのか分かりにくい

そのため、LLM内部の記憶に依存せず、外部の構造化Memoryを持つ設計にする。

## 対象範囲

対象はPCに限定しません。

```text
PC
RC
Mobile
Game
Health
Shopping
Life
Project
Reference
```

ドメインは違っても、記録・検索・判断の構造は共通です。

## 目指す体験

ユーザーが相談したときに、AIが以下を踏まえて回答する。

- 過去に何を試したか
- その結果どうなったか
- どの仮説が残っているか
- 何が未確認か
- 現在の判断・方針は何か
- どの根拠に基づくか

## 非目標

以下は当面の目標にしません。

- 完全自律で正本を更新するAI
- すべてをLLMの内部記憶に任せる設計
- PC専用トラブルシュートBot
- 単なるMarkdown検索ツール
- ツールありきの設計
'@

$files["$Root\docs\00_Project\Goals.md"] = @'
# Goals

## 最上位ゴール

```text
ユーザーに関する全領域の情報を、
正確に記録・検索・判断・回答できる
Personal Local Secretary AI を構築する。
```

## Phase別ゴール

### Phase 0: 設計基盤

- プロジェクトの目的を明文化する
- 用語を定義する
- 重要原則を決める
- Gitで設計資産を管理する

### Phase 1: Memory DB MVP

- Airtableに基本テーブルを作る
- SourcesとPending Claimsを作る
- AI抽出結果を人間がレビューできる状態にする

### Phase 2: n8n + Local LLM

- n8nからOllama/Qwenを呼び出す
- 入力ログからclaims JSONを作る
- Pending Claimsへ登録する

### Phase 3: Cloud Review

- confidenceが低いclaimや重要claimをChatGPT/Claudeでレビューする
- review済みclaimをPending Claimsへ保存する

### Phase 4: Ask Workflow

- Airtableの構造化情報を検索する
- 必要に応じてRaw Memoryを参照する
- 回答時に実施済み・仮説・判断を分ける

### Phase 5: Corpus2Skill実験

- accepted済みKnowledgeや過去ログをSkill Tree化する
- Airtable検索とCorpus2Skill navigationを併用する

### Phase 6: Supabase/PostgreSQL移行検討

- Airtableで限界が出た場合、正本DBをPostgreSQL/Supabaseへ移行する

## 成功条件

- 過去に実施済みの提案を繰り返さない
- MainPC/SubPCなどのentityを混同しない
- PC以外のdomainにも展開できる
- 事実・仮説・判断・結果を分離できる
- 根拠となるSourceを追跡できる
- AIが正本を勝手に更新しない
'@

$files["$Root\docs\00_Project\Requirements.md"] = @'
# Requirements

## Must

- ユーザーに関する全領域を扱える
- domain/entityを分けて記録できる
- Sourceを保存できる
- AI抽出結果をPending Claimsとして保存できる
- 人間レビューを挟める
- accepted/rejected/needs_editを管理できる
- 事実・仮説・判断・結果を分離できる
- 現在有効な属性や判断を検索できる
- 過去のActionとResultを検索できる
- LLMやツールを交換可能にする

## Should

- ローカルLLMを日常処理に使える
- Cloud LLMをレビュー用途に使える
- n8nで処理フローを組める
- AirtableでレビューしやすいUIを作れる
- Corpus2Skillなどで過去ログのナビゲーションを補助できる
- 将来Supabase/PostgreSQLへ移行できる

## Could

- Graphiti/Zep/Mem0などのAgent Memoryを追加する
- 自作Web UIを作る
- MCP化してChatGPTから呼べるようにする
- ObsidianやMarkdown exportを用意する

## Won't

少なくとも初期段階では以下は行わない。

- AIによる正本データの無承認更新
- LLM内部メモリを正本にする
- すべての情報をRAGだけで処理する
- PC専用設計に固定する
- いきなり完全自律エージェント化する
'@

$files["$Root\docs\00_Project\Principles.md"] = @'
# Principles

## 1. LLMは記録の正本にしない

LLMの内部記憶や会話履歴を正本にしない。

正本は、Airtable、将来のDB、Raw Archiveなど、外部で管理できる形に置く。

## 2. AIは候補を作り、人間が承認する

AIは抽出・分類・要約・候補作成を行う。

正本への反映は、Pending Claimsを経由し、人間レビューを前提とする。

## 3. 事実・仮説・判断を分離する

以下を混同しない。

- Observation
- Action
- Result
- Hypothesis
- Decision
- Open Question

特に仮説を確定原因として扱わない。

## 4. ドメイン非依存に設計する

PCは代表ユースケースにすぎない。

RC、Mobile、Health、Shopping、Life、Projectなどにも同じ構造を適用できるようにする。

## 5. ツール依存ではなく設計原則を優先する

Airtable、n8n、Ollama、ChatGPT、Claude、Corpus2Skillは手段である。

ツールが変わっても、記録・検索・判断の構造は維持する。

## 6. Raw Sourceを残す

AIが抽出した情報だけを残さない。

必ず元ログ・元資料・入力テキストをSourceとして残す。

## 7. 現在値と履歴を分ける

現在有効な属性や判断と、過去の履歴を混同しない。

例:

- 現在のCPU
- 過去に試した設定
- 以前は有効だったが今は使っていない判断

## 8. 再現性を重視する

設計書、プロンプト、ワークフロー、スクリプトはGitで管理する。

なぜそう決めたかをADRとして残す。

## 9. 小さく始めて差し替え可能にする

最初から完成形を作らない。

Airtableで始め、必要に応じてPostgreSQL/Supabaseへ移行できる設計にする。

## 10. 秘書AIを育てる

このプロジェクトは一度作って終わりではない。

モデル、ツール、記録方式の進化に合わせて育てる長期プロジェクトとして扱う。
'@

$files["$Root\docs\00_Project\Glossary.md"] = @'
# Glossary

## Domain

情報の大分類。

例:

- PC
- RC
- Mobile
- Game
- Health
- Shopping
- Life
- Project
- Reference

## Entity

対象物・対象テーマ。

例:

- MainPC
- SubPC
- Google Play Games
- RC Car
- iPhone
- Mobile Plan

## Attribute

Entityに紐づく属性。

例:

- CPU
- GPU
- Motor
- Carrier
- Contract Plan

## Source

元情報。

例:

- 会話ログ
- 手入力メモ
- PowerShell出力
- Webページ
- スクリーンショット
- メール

## Claim

AIがSourceから抽出した未承認の記録候補。

## Pending Claim

レビュー待ちのClaim。

## Observation

観測事実。

## Action

実施したこと。

## Result

Actionの結果。

## Hypothesis

仮説。未検証または未確定の説明。

## Decision

判断・方針・結論。

## Issue

問題・未解決テーマ・検討対象。

## Raw Memory

元ログや元資料をそのまま保存する層。

## Structured Memory

AirtableやDBに保存する構造化されたMemory。

## Agent Memory

LLMが処理中に参照する記憶・検索補助層。

## Skill Navigation Layer

Corpus2Skillなどで作る、文書群や過去ログを辿りやすくする知識の地図。
'@

$files["$Root\docs\01_Architecture\Overall.md"] = @'
# Overall Architecture

## 現在の基本構成

```text
Airtable + n8n + Local Qwen/Ollama + ChatGPT/Claude
```

## 全体像

```text
[Input]
  手入力メモ
  ChatGPTログ
  PC検証ログ
  RCメモ
  Mobileメモ
  Web調査結果

↓ n8n

[Preprocess]
  日付付与
  Source保存
  domain候補推定
  長文なら分割

↓ AI Router

[Local LLM]
  一次抽出
  claims JSON生成
  confidence付与

↓ 条件分岐

[Cloud LLM]
  必要時のみレビュー
  矛盾・分類・重要度チェック

↓ n8n

[Airtable]
  Pending Claimsに保存

↓ Human Review

[Structured Memory]
  Entities
  Issues
  Actions
  Results
  Hypotheses
  Decisions
```

## 役割

- Airtable: 構造化Memory DB / 人間レビューUI
- n8n: Orchestrator / AI Router
- Local LLM: 一次抽出Worker
- ChatGPT/Claude: 高精度Reviewer
- Corpus2Skill: 将来のSkill Navigation Layer候補
'@

$files["$Root\docs\01_Architecture\Memory.md"] = @'
# Memory Architecture

## 3層構成

```text
1. Raw Memory
2. Structured Memory
3. Agent Memory
```

## Raw Memory

元情報をそのまま保存する。

例:

- 会話ログ
- メモ
- コマンド出力
- 調査ログ
- スクリーンショット説明
- Web調査結果

## Structured Memory

Airtableまたは将来DBに保存する構造化データ。

主なテーブル:

- Domains
- Entities
- Attributes
- Issues
- Actions
- Results
- Hypotheses
- Decisions
- Sources
- Pending Claims

## Agent Memory

LLMが回答や処理中に使う補助Memory。

候補:

- Corpus2Skill
- Mem0
- Zep
- Graphiti
- 自作Memory Layer

## 原則

正本はStructured Memoryに置く。

Agent MemoryやLLM内部Memoryは正本にしない。
'@

$files["$Root\docs\01_Architecture\Corpus2Skill.md"] = @'
# Corpus2Skill

## 位置づけ

Corpus2Skillは、Airtable/DBの代替ではなく、知識の地図として使う候補。

```text
Airtable / DB = 記録の正本
Corpus2Skill = 知識の地図
n8n = 業務フロー
LLM = 読解・分類・回答
```

## 効くところ

- 過去ログの地図化
- 長文資料の階層化
- 設計資料のナビゲーション
- 複数domain横断の文脈探索
- LLMが「どこを見ればいいか」判断する補助

## 足りないところ

Corpus2Skillだけでは以下を担当しない。

- 新しい情報を正確に記録する
- accepted/rejected/needs_editを管理する
- 現在値を持つ
- Action/Resultを台帳化する
- 仮説と確定原因を分ける
- 正本を管理する

## 導入タイミング

v0.2以降で実験する。

```text
v0.1:
  Airtable + n8n + Local/Cloud LLMでPending Claims作成

v0.2:
  accepted records / 既存Markdown / 過去ログをexport

v0.3:
  Corpus2SkillでSkill Tree化

v0.4:
  Airtable検索 + Corpus2Skill navigationを併用
```
'@

$files["$Root\docs\02_Database\Airtable.md"] = @'
# Airtable Design

## 役割

Airtableは初期MVPにおける構造化Memory DB兼人間レビューUI。

## 初期テーブル

- Domains
- Entities
- Sources
- Pending Claims
- Issues
- Attributes
- Actions
- Decisions

## Pending Claims

中心となるテーブル。

AIが抽出した未承認候補をここに入れる。

主なフィールド:

```text
Statement
Claim Type
Source
Domain Guess
Entity Guess
Issue Guess
Confidence
Reason
Review Status
Reviewer Notes
Accepted To
Extractor
Review Level
Model Output Raw
Model Name
Prompt Version
Created At
Processed At
```

## Review Status

```text
pending
accepted
rejected
needs_edit
```

## Claim Type

```text
attribute
issue
observation
action
result
hypothesis
decision
open_question
note
```

## Extractor

```text
local_qwen
openai
claude
manual
```
'@

$files["$Root\docs\02_Database\EntityModel.md"] = @'
# Entity Model

## 共通モデル

```text
Domain
Entity
Attribute
Event
Issue
Observation
Action
Result
Hypothesis
Decision
Source
Claim
```

## 設計意図

PC、RC、Mobileなどはdomainが違うだけで、記録・検索・判断の基本構造は同じ。

## 例: PC

```text
Domain: PC
Entity: MainPC
Issue: GPG動作遅延
Action: GPG再インストール
Result: 初回のみ改善、その後再発
Hypothesis: 仮想環境状態劣化の可能性
Decision: 単純な再インストール提案は低優先
```

## 例: RC

```text
Domain: RC
Entity: RC Car A
Issue: 高速域でふらつく
Action: トー角変更
Result: 低速は改善、高速は未改善
Hypothesis: リアグリップ不足
```

## 例: Mobile

```text
Domain: Mobile
Entity: Mobile Phone
Issue: バッテリー消費が早い
Action: アプリ別使用量確認
Result: 特定アプリのバックグラウンド通信が多かった
Hypothesis: 通知同期が原因の可能性
```
'@

$files["$Root\docs\03_Workflows\Recording.md"] = @'
# Recording Workflow

## 目的

入力ログやメモから、後で検索・確認できるPending Claimsを作る。

## フロー

```text
Input
↓
n8n
↓
Source作成
↓
Local LLMで一次抽出
↓
必要ならCloud LLMレビュー
↓
Pending Claims作成
↓
Human Review
↓
正式テーブルへ反映
```

## 原則

AIは正式テーブルを直接更新しない。

まずPending Claimsへ入れる。
'@

$files["$Root\docs\03_Workflows\Answer.md"] = @'
# Answer Workflow

## 目的

質問に対して、過去履歴・実施済み・仮説・判断を踏まえて回答する。

## フロー

```text
Question
↓
domain/entity/intent推定
↓
Airtable検索
↓
関連Issue/Action/Decision/Hypothesis取得
↓
必要ならRaw参照
↓
必要ならCorpus2Skill navigation
↓
Local LLMで回答下書き
↓
重要回答はCloud LLMレビュー
↓
Answer
```

## 目標

- 実施済みの提案を繰り返さない
- domain/entityを混同しない
- 事実・仮説・判断・未確認を分ける
- 根拠を示す
'@

$files["$Root\docs\04_AI\Workers.md"] = @'
# AI Workers

## Local LLM Worker

担当:

- 一次抽出
- domain/entity推定
- claims JSON生成
- 短い要約
- 低リスク分類

候補:

- Qwen系ローカルモデル
- Ollama

## Cloud LLM Reviewer

担当:

- 高精度レビュー
- 長文ログ整理
- 矛盾チェック
- 重要Decision整理
- 設計相談

候補:

- ChatGPT
- Claude

## Orchestrator

担当:

- 入力の種類判定
- Local/Cloudのルーティング
- Airtable登録
- Review状態に応じた処理

候補:

- n8n
- 将来 LangGraph など
'@

$files["$Root\docs\05_Implementation\Runtime.md"] = @'
# Runtime

## 役割

`local-secretary-runtime` は実装リポジトリ。

担当:

- Docker
- n8n
- Python scripts
- API
- config
- tests

## 原則

- 設計書は `local-secretary-ai` に置く
- 実装コードは `local-secretary-runtime` に置く
- RawデータやDB本体はGitに入れない
'@

$files["$Root\docs\06_Research\LLM.md"] = @'
# LLM Research

## 現時点の方針

```text
Local LLM = 日常処理の一次Worker
Cloud LLM = 高精度レビュー・難しい統合・設計相談
```

## 評価軸

- JSON安定性
- 日本語理解
- 長文ログ整理
- 事実/仮説/判断の分離
- ローカル実行性能
- コスト
- プライバシー
'@

$files["$Root\docs\07_Meetings\2026-09-26.md"] = @'
# Meeting Note: 2026-09-26

## 議題

Local Secretary AI の設計書リポジトリと実装リポジトリを分ける。

## 決定

- `local-secretary-ai`: 設計・仕様・プロンプト・判断履歴
- `local-secretary-runtime`: 実装・n8n・Python・Docker
- `data`: Raw/import/export/backupなど。Gitには入れない
- `playground`: 検証用

## 次アクション

- 設計書テンプレートを作成
- Git初期コミット
- Runtime側にsetup/doctor系スクリプトを整備
'@

$files["$Root\decisions\ADR-0001-use-design-and-runtime-repos.md"] = @'
# ADR-0001: 設計リポジトリと実装リポジトリを分ける

## Status

Accepted

## Context

Local Secretary AI は長期プロジェクトであり、設計思想・データモデル・AIプロンプト・判断履歴が重要な資産になる。

一方、実装はn8n、Ollama、Python、Dockerなど技術変化の影響を受けやすい。

## Decision

以下の2リポジトリ構成にする。

```text
local-secretary-ai       = 設計・仕様・プロンプト・判断履歴
local-secretary-runtime  = 実装・自動化・スクリプト・Docker
```

## Consequences

- 設計を長期的に維持しやすい
- 実装を差し替えても設計思想を残せる
- ドキュメントとコードの責務が明確になる
'@

$files["$Root\decisions\ADR-0002-use-pending-claims.md"] = @'
# ADR-0002: AI抽出結果はPending Claimsに保存する

## Status

Accepted

## Context

LLMは有用だが、誤抽出・推測・事実と仮説の混同が起こり得る。

Personal Secretary AI では誤記録が危険である。

## Decision

AIは正本テーブルを直接更新しない。

まずPending Claimsへ保存し、人間がレビューする。

## Consequences

- 正確性を維持しやすい
- AIの誤りを人間が修正できる
- 処理は一手間増える
- 長期的な信頼性が上がる
'@

$files["$Root\templates\ADR-template.md"] = @'
# ADR-XXXX: タイトル

## Status

Proposed / Accepted / Superseded / Rejected

## Context

なぜこの判断が必要か。

## Decision

何を決めたか。

## Consequences

良い影響、悪い影響、今後の注意点。
'@

$files["$Root\templates\Meeting-template.md"] = @'
# Meeting Note: YYYY-MM-DD

## 議題

## 決定事項

## 未決事項

## 次アクション

## メモ
'@

foreach ($path in $files.Keys) {
    Write-Doc -Path $path -Content $files[$path]
}

Write-Host ""
Write-Host "Done."
Write-Host "Next:"
Write-Host "  cd $Root"
Write-Host "  git status"
Write-Host "  git add ."
Write-Host "  git commit -m `"Add initial project documentation`""
