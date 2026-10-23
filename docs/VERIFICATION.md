# VERIFICATION — Runtime検証入口

テスト実装はroot `tests/`と各アプリケーションの既存テスト、CIは`.github/workflows/`を使用する。テスト対象・実行準備は[README](../README.md)、[SOURCE_MAP](SOURCE_MAP.md)、対象CIを確認する。

Python単体・PostgreSQL隔離・Vue build/Playwright・WSLとWindows実機・実MAGI/DBは証拠レベルを分ける。文書統一だけで既存サービス・DB・Docker・Secretを操作しない。
