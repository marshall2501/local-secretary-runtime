# Agent instructions — Personal Local Secretary AI runtime

## Read before acting

新しい主要作業や作業選定時は、設計repo `local-secretary-ai/main` の現行 [START_HERE](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/START_HERE.md) を入口に、`PROJECT → ARCHITECTURE → STATUS → PLAN` を確認する。必要な個別設計、DesignDecisions、DevelopmentKnowledgeは今回の作業に関係する部分だけ追加確認する。

作業を選定した後の変更レベル、change plan、traceability、安全・検証、同一plan内の不具合修正、完了条件は設計repoの [DEVELOPMENT_RULES](https://github.com/marshall2501/local-secretary-ai/blob/main/DEVELOPMENT_RULES.md) を正本とする。Level B / C の個別planは設計repoの `docs/templates/change-plan-template.md` を基準にする。

設計repoを取得できない場合は、その旨を明示する。ローカルに残る古い文書や過去の会話を「最新版」と呼ばず、高影響変更は正本を確認できるまで安全側で扱う。

読了順は判断上の優先順位ではない。本人の最新指示とPROJECTの目的・要件、採用中のARCHITECTURE、安全条件、STATUSの実測を照合して作業を選ぶ。PLANや古いREADME、過去の「次の操作」を自動実行命令として扱わない。

## Product boundary

最終目標は、PC・ゲーム・RC・スマホ・健康・買い物・生活・予定・各種プロジェクトを横断し、自然言語の依頼から記憶・調査・計画・権限内の安全な実行・結果検証・記録・継続対応まで行うPersonal Local Secretary AIである。独立PKBは先行して日常利用できる能力だが、統合秘書AIの完成とは区別する。

RITSUKO = Secretary Core / Orchestrator。Task、履歴、Observation、権限、利用可能な能力、Action / Result、停止・再開、最終判断を管理する。MAGIのLLMは意味理解・分析・提案を行う交換可能な部品であり、LLMの成功宣言だけで目的達成、事実化、権限付与を確定しない。

## Runtime source boundary

source directoryの責務・dependency directionは設計repoの `docs/01_Architecture/RuntimeSourceArchitecture_実装ソース構造.md` を正本とする。現在実在するfile treeと機能→source対応は本repoの `docs/SOURCE_MAP.md` を現行実装索引とする。

構成整理後に「改善前まで使えるように戻す」と言われても、旧source layoutや旧packageへrevertしない。現在の責務別構成を維持したまま、以前利用できた能力を新構成上で再成立させる。

## Memory and database boundary

構造化記憶の正本は現行PostgreSQL一つ。全件・厳密条件・履歴・時点検索はSQLを基本とし、RAG・graph・vector indexは補助として扱う。明確な低リスク本人申告は決定的なMemory Intakeゲートを通して自動登録可能とし、重大な矛盾・曖昧さ・対象不明・高影響などを例外Pendingへ送る。外部操作の承認と記憶登録の判断を混同しない。

ローカル内部Runtimeでは、機能ごとのDB writer role細分化を成果としない。管理・復旧用accessは通常Runtimeから分離するが、通常Runtimeは共通DB access boundaryを使える。ChatGPT / MCP / Web API等の外部入口、外部変更Action、Secret、高影響操作はApplication / API公開surface / RITSUKO Policy・Approval・Executor等の操作境界で制御する。内部DB roleを追加分割するのは具体的な運用上の必要性が確認できた場合だけとする。

稼働中DBへ適用済みのmigration履歴は書き換えない。意図的にclean replacement DBを作る場合は、旧migration列を履歴資産として保全したうえで、現在採用schemaのbaselineを新しい正本として作り直してよい。運用DBや実データへ影響する変更はDEVELOPMENT_RULESとDesignDecisions D-08に従う。

## Runtime-specific safety

- Secret、個人原本、DB dump、個人ログをGitHubやチャットへ登録しない。
- `yt-topic-search` を変更・停止しない。
- サブPCを通常の開発・稼働先、メインPCを手動復旧先として扱う。
- 利用者にサブPC操作を依頼する場合は、原則一度に一操作だけ提示し、結果を確認して次を選ぶ。
- GitHub反映、offline/unit、隔離DB、サブPC実機、統合、日常利用を同じ成功として扱わない。
- 目標図やコードの存在だけを実装済み・実証済みの証拠にしない。

## Technology

PostgreSQL、FastAPI、Docker、Ollama、クラウドLLM、MCP、n8n等は目的ではなく交換可能な手段として扱う。n8nをSecretary Coreそのものとは扱わない。

## Documentation

大目標・製品要件はPROJECT、採用構成はARCHITECTURE、実装・検証の現在地はSTATUS、現在選定した主要作業と受入条件はPLAN、開発プロセスはDEVELOPMENT_RULES、個別のLevel B / C変更はWorkPlanを正本とする。

意味のある実装・検証結果が出ても、コード変更だけでSTATUSの実機成功を宣言しない。一次証拠を確認した後、設計repoの文書責務に従って更新する。
