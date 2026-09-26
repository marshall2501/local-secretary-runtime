# ③ 記憶参照：SQL-first 全件列挙・条件検索（MVP）

この段階で加えた `GET /memory/search` は既存のPostgreSQLの記憶を
**LLMやRAGを介さず直接照会**する読み取り専用APIです。ヒットしない情報を
AIが補完することはありません。外部Web調査・原文ファイル検索は未実装です。

## 実装ファイル

- `api/secretary_api.py` — 既存APIと同じBearer認証、ローカルDB権限を利用。
- `tests/test_memory_search.py` — SQL固定文字列、パラメータ化、一覧とページングのオフラインテスト。
- `docs/memory-search.md` — 本手順。

メインPC上の基点：`D:\AI\projects\local-secretary-runtime`

## 対象・境界

- Claim：デフォルトは有効期間内の `current_claims`。必要なら `include_history=true` で
  期限切れ・置換前などの `claims` 表全体を表示します。**現在のClaimもverifiedとは限りません**。
- Issue、Hypothesis：診断の事実や確定記憶と混ぜず、`kind` と `state` を分離。
- Source：DBにあるメタデータ（引用・URI・種別）のみ。長文や元ファイルは未保存。
- Pending Claims：未承認候補は通常の記憶検索に混ぜません。人間専用の
  `scripts/memory/review.py pending` で別途一覧します。
- `domain`：Entityに紐づくClaim/Issue/Hypothesisだけが対象。
  Sourceは複数domainから参照されるためdomain未設定として扱い、domain絞り込み時は対象外です。

検索文字列 `q` を省略すると対象の全件を順番に列挙できます。指定した場合は、
Entity名・タイトル・値・根拠文・Source引用・URIに対する**大文字小文字を区別しない
部分一致**です。ワイルドカードや意味的類似度を解釈しません。
1ページ最大100件、`total` は一致した総件数です。
データ更新中はページ間で件数・順序が変わる可能性があるため、
**厳密な時点固定の全量エクスポートではありません**。厳密な取得にはバックアップ等を用います。

## メインPCでの確認

PRがmainにマージされてから：
```powershell
cd D:\AI\projects\local-secretary-runtime
git status
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_memory_search.py -v
```

既存のAPIをCtrl+Cで停止して再起動します。APIのPowerShellにはこれまでの
`LSA_API_DSN` と `LSA_API_TOKEN` が設定されている必要があります。

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.secretary_api:app --host 127.0.0.1 --port 8010 --no-access-log
```

同じトークンを使うAPIクライアント側のPowerShellで：

```powershell
$base = "http://127.0.0.1:8010"
# $headers は従来通り API Bearer token を設定する。チャットにトークンを貼らない。
$all = Invoke-RestMethod "$base/memory/search?limit=20&offset=0" -Headers $headers
$all | Format-List total,limit,offset,include_history
$all.items | Format-Table kind,domain,entity_name,title,state,recorded_at -AutoSize

# Sourceを含む全domainから literal な部分一致検索
Invoke-RestMethod "$base/memory/search?q=ram_gb&limit=50" -Headers $headers

# PC domainのClaimだけを検索（未検証のClaimも返り得る）
Invoke-RestMethod "$base/memory/search?domain=pc&kind=claim&limit=50" -Headers $headers

# 期限切れ・旧Claimも含む履歴照会
Invoke-RestMethod "$base/memory/search?kind=claim&include_history=true&limit=50" -Headers $headers
```

この段階では、以前の試験用タスクは記憶検索の対象ではありません。
Claimが0件なら、`total=0` が正常です。前段の `docs/memory-review.md` の
架空データの承認テストを完了した後なら、承認した `ram_gb` が照会できます。

## 次の段階

③：Sourceの原本保存とハッシュ検証、外部Web調査で収集した出典の登録。
④：独立した外部操作の承認、Policy & Approvalによる実行直前の検証、
無害な模擬ツールによる監査付き実行。既存の外部プロジェクト・Dockerコンテナは変更しません。
