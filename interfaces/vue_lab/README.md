# Vue / FastAPIの秘書AI UI比較（隔離試作）

このディレクトリは **`experiment/vue-secretary-ui-20261015` 専用の技術比較**です。
RITSUKOの既存 `StateDrivenTaskApplication` と `run_observation_loop` / `resume_user_answer` を呼び出しますが、
**Task Store・LLM応答・ユーザー質問は全て合成fixture**です。実PostgreSQL・MAGI・PKB・Actionには接続しません。

## 目的

ブラウザがFastAPI経由で共通RITSUKO Applicationを呼び出し、
Taskの受付（202）→実行中→本人質問待ち→同一Task IDで回答→架空完了、
および停止・F5後の再読込を画面とAPIの両方で試すこと。

**この試作では未達成**：実Taskの永続化、MAGI実通信、複数プロセスWorker、
実行途中のバックエンド再起動をまたぐ処理継続、Proposal Review、PVAとの実接続。
画面の再接続成功と、RITSUKO Taskが実際に完遂した事実は別です。

## 安全な起動（サブPCで確認するときのみ）

専用ブランチの別checkout / worktreeなど、**通常運用中のソースを上書きしない隔離環境**で扱う。
既存8093（NiceGUI）・8010（実Secretary API）・8015（PVA）は使用しない。

1. Python依存は既存runtimeの `requirements-web.txt` に従う。
2. `interfaces/vue_lab/frontend`で`npm install`、`npm run build`。
3. ソースルートのPowerShellで以下を設定して起動する。トークンは架空試作専用で、Gitやチャットに貼り付けない。

   ```powershell
   $env:LSA_VUE_LAB_TOKEN = [guid]::NewGuid().ToString("N")
   $env:LSA_VUE_LAB_PORT = '8098'
   & ".\.venv\Scripts\python.exe" -m interfaces.vue_lab.app
   ```

4. 同じPCの `http://127.0.0.1:8098/ui/` を開いて試作専用トークンでログイン。
   F5でCookie認証とURL内Task IDを利用し、同じ架空Taskを再表示できる。
5. Vite開発サーバーを使うときは別PowerShellで `npm run dev`、
   API側へ `LSA_VUE_LAB_DEV_ORIGIN=http://127.0.0.1:5173` を設定してからAPIを起動する。
   アクセス先は `http://127.0.0.1:5173/ui/`。

**重要**：試作用Tokenはサービス終了時に使い捨てる。CookieはHttpOnly・SameSite=Strictで、
JavaScriptにはTokenを永続保存しない。Web APIはlocalhost限定、Origin/CSRF制限あり。
一般のLAN/Internetへの公開と外部利用は認可していない。
本番DB/Secretを渡さない。実PKB/Financeデータを入力しない。

## 自動検証

```powershell
& ".\.venv\Scripts\python.exe" -m unittest discover -s tests -p test_vue_lab.py -v
```

フロントエンドは `npm run build` でTypeScriptの型検査とViteビルドを実行。
GitHubの隔離ワークフローはPRで起動する。テストPASSは実Windowsブラウザ受入の代わりではない。

## 後続設計の判断

- 現行NiceGUIと比較するのは初回受付・本人回答・停止・画面F5・API断の表示・
  画面だけのソース更新・開発コスト。
- 現行API `/tasks` の意味は変更しない。実RITSUKO日常Taskへの接続は
  Task lifecycle/policy/persistenceを踏まえた追加設計・受入が必要。
- Vue採用の最終判断と複数システム共通UIは「AI開発基盤」プロジェクトの担当。
