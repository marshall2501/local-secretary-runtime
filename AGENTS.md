# Agent instructions — Personal Local Secretary AI runtime

## Read before acting

新しい主要作業や作業選定時は、設計repo `local-secretary-ai/main` の現行 [`START_HERE`](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/START_HERE.md) を入口に、`PROJECT → ARCHITECTURE → STATUS → PLAN` を確認する。必要な個別設計、DesignDecisions、DevelopmentKnowledgeは今回の作業に関係する部分だけ追加確認する。

読了順は判断上の優先順位ではない。本人の最新指示とPROJECTの目的・要件、採用中のARCHITECTURE、安全条件、STATUSの実測を照合して作業を選ぶ。PLANや古いREADME、過去の「次の操作」を自動実行命令として扱わない。

## Product boundary

最終目標は、PC・ゲーム・RC・スマホ・健康・買い物・生活・予定・各種プロジェクトを横断し、自然言語の依頼から記憶・調査・計画・権限内の安全な実行・結果検証・記録・継続対応まで行うPersonal Local Secretary AIである。独立PKBは先行して日常利用できる能力だが、統合秘書AIの完成とは区別する。

RITSUKO = Secretary Core / Orchestrator。Task、履歴、Observation、権限、利用可能な能力、Action/Result、停止・再開、最終判断を管理する。MAGIのLLMは意味理解・分析・提案を行う交換可能な部品であり、LLMの成功宣言だけで目的達成、事実化、権限付与を確定しない。

## Memory and control

構造化記憶の正本は現行PostgreSQL一つ。全件・厳密条件・履歴・時点検索はSQLを基本とし、RAG・グラフ・ベクトル索引は補助として扱う。明確な低リスク本人申告は決定的なMemory Intakeゲートを通して自動登録可能とし、重大な矛盾・曖昧さ・対象不明・高影響などを例外Pendingへ送る。外部操作の承認と記憶登録の判断を混同しない。

## Safety and validation

- GitHub反映、単体/オフラインテスト、隔離DB試験、サブPC実機、統合動作、日常利用での成功を区別する。
- 目標図やコードの存在だけを実装済み・実証済みの証拠にしない。
- 架空・隔離試験と運用DB・実データ・高影響操作の安全ゲートを分ける。
- Secret、個人原本、DBダンプ、個人ログをGitHubやチャットへ登録しない。
- `yt-topic-search` を変更・停止しない。
- サブPCを通常の開発・稼働先、メインPCを手動復旧先として扱う。
- ユーザーにサブPC操作を依頼する場合は、原則一度に一操作だけ提示し、結果を確認して次を選ぶ。

## Simplification, recovery, and internal database boundaries

「無駄を省く」「簡単にする」「改善する」は、変更行数、機能範囲、DB権限、検証範囲を小さくする指示ではない。同じ責務・SQL・設定・接続処理・変換・検証を機能ごとに重複実装せず、不要になった旧経路を整理し、全体として一貫して使える状態にすることを優先する。

構成整理後に「改善前まで使えるように戻す」と言われた場合、旧source layoutや旧packageへrevertしない。現在の責務別構成を維持し、以前利用できた機能を新構成上で再成立させる。永続化構造が旧実装の履歴に引かれて複雑化した場合、現在採用schemaから新しいDBを作成し、必要データを選別移植してよい。

ローカル内部Runtimeでは、機能ごとのDB writer role細分化を成果としない。管理・復旧用accessは通常Runtimeから分離するが、通常Runtimeは共通DB access boundaryを使える。ChatGPT / MCP / Web API等の外部入口、外部変更Action、Secret、高影響操作はAPI公開surface、認証、RITSUKO Policy / Approval / Executor等の操作境界で制御する。内部DB roleを追加分割するのは具体的な運用上の必要性が確認できた場合だけとする。

## Development rules

実装前に、目的への貢献、変更対象、期待する挙動、失敗条件、検証方法を明確にする。同じ条件で確認済みの証拠は再利用し、条件が変わった過去実験を一般化しない。

技術やフレームワークを目的化しない。PostgreSQL、FastAPI、Docker、Ollama、クラウドLLM、MCP、n8n等は必要に応じて変更・置換できる手段として扱う。n8nをSecretary Coreそのものとは扱わない。

稼働中DBへ適用済みのmigration履歴は書き換えない。一方、意図的にclean replacement DBを作る場合は、旧migration列を履歴資産として保全したうえで、現在採用schemaのbaselineを新しい正本として作り直してよい。運用DBや実データへ影響する変更では、必要なバックアップ・復元・権限・承認を確認する。

## Documentation

大目標・製品要件はPROJECT、採用構成はARCHITECTURE、実装・検証の現在地はSTATUS、現在選定した作業と受入条件はPLANを正本とする。runtime READMEや個別docsへ進捗履歴を重複して固定しない。

意味のある実装・検証結果が出た場合も、コード変更だけでSTATUSの実機成功を宣言しない。一次証拠を確認した後、設計repoの責務に従って更新する。
