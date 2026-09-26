# Local Secretary Runtime

Personal Local Secretary AI の実装リポジトリです。

このリポジトリには、実際に動作するスクリプト、n8n workflow、Docker構成、Pythonコード、API、設定ファイルを置きます。

## 対応する設計リポジトリ

- local-secretary-ai

## 役割

- n8n workflow
- Ollama / Local LLM連携
- Airtable連携
- Python scripts
- Docker構成
- 環境構築スクリプト
- 診断スクリプト
- バックアップ / import / export

## 原則

- `.env` やAPIキーはGitに入れない
- Raw data、DB本体、モデル本体はGitに入れない
- 設計思想やADRは `local-secretary-ai` 側に置く
- このリポジトリは「動くもの」を管理する
