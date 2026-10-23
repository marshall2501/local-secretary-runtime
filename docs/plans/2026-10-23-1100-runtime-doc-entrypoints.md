# Runtime — 共通文書入口統一

状態: IMPLEMENTING / 2026-10-23。Level C横断承認のうち、Runtimeの文書入口のみ対象。
横断計画: https://github.com/marshall2501/ai-development-playbook/blob/main/docs/plans/2026-10-22-2055-unified-repository-layout.md

- 基準main: `0c3ba8850482b775350192b70a93a1d256a9c14f`、退避 `backup/pre-layout-unification-20261023`。
- root `AGENTS.md`をPlaybook URL参照と固有ルール/START_HEREへの短い案内へ整理する。
- `docs/{START_HERE,OVERVIEW,ARCHITECTURE,STATUS,PLAN,VERIFICATION}.md`を共通名で追加し、製品設計マスタの別repo参照を維持する。
- `shared/`・submoduleはもともとこのrepoのmainに存在しない。無理に追加・削除しない。
- `local-secretary-ai`の設計・Runtime統合Draft PR #4のレビュー/マージ、運用パス切替は本変更で実施しない。
- DB/Runtime/launcher/CI/Secret/Deploy操作なし。Python/CIの既存機能に変更なし。
- GitHubの相対リンク、追跡ファイル、既存CI非変更を確認し、設計+Runtime統合時に入口の重複を再整理する。
