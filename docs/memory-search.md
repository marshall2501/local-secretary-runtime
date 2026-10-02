# SQL-first Memory Search

`GET /memory/search` は `api/secretary_api.py` が提供する、PostgreSQL正本を直接読む読み取り専用検索です。LLMやRAGによる意味推測を挟まず、保存されているEntity / Claim / Issue / Hypothesis / Sourceメタデータを条件検索・列挙します。

## 境界

- デフォルトのClaim検索は現在有効な記録を対象にし、`include_history=true` で履歴を含められます。
- `current` であることと `verified` であることは同義ではありません。`verification_status` 等の不確実性を呼び出し側で保持してください。
- Issue / HypothesisはClaimと別のkind/stateとして返し、確定事実へ暗黙昇格しません。
- Pending候補は通常のMemory検索結果へ混ぜません。Pendingはそれを所有するMemory Intake / PKB側の例外確認経路で扱います。
- SourceはDBに保存されたメタデータを返します。原本アーカイブの有無は別機能の状態に依存します。
- `q` はliteralな部分一致検索です。意味的類似検索やLLM補完ではありません。
- ページング中にDBが更新されれば結果集合が変わる可能性があります。厳密な時点固定取得にはDBスナップショット等を使います。

## 主なパラメータ

- `q`: Entity名、タイトル、値、根拠、Source引用・URI等に対する部分一致。
- `domain`: Entityに紐づくdomainで絞り込み。
- `kind`: `claim` 等の種別。
- `include_history`: Claim履歴を含める。
- `limit` / `offset`: ページング。1ページ最大100件。

## localhostでの確認

APIをWindows venvで直接起動する場合:

```powershell
cd D:\AI\projects\local-secretary-runtime
.\.venv\Scripts\python.exe -m uvicorn api.secretary_api:app --host 127.0.0.1 --port 8010 --no-access-log
```

`LSA_API_DSN` と `LSA_API_TOKEN` はローカル環境で安全に設定し、SecretをチャットやGitへ貼らないでください。Docker APIを使う場合は [`portable-api.md`](portable-api.md) を参照します。

別PowerShellからBearer headerを用意した例:

```powershell
$base = 'http://127.0.0.1:8010'
$all = Invoke-RestMethod "$base/memory/search?limit=20&offset=0" -Headers $headers
$all | Format-List total,limit,offset,include_history

Invoke-RestMethod "$base/memory/search?q=ram_gb&limit=50" -Headers $headers
Invoke-RestMethod "$base/memory/search?domain=pc&kind=claim&limit=50" -Headers $headers
Invoke-RestMethod "$base/memory/search?kind=claim&include_history=true&limit=50" -Headers $headers
```

このendpointの仕様変更は `api/secretary_api.py` と対応テストを正本とし、実装済み・実機検証済みの範囲は設計repoの `STATUS` で管理します。
