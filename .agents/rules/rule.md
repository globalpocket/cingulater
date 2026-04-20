---
trigger: always_on
generationConfig:
temperature: 0.1
  topK: 20
  topP: 0.90
  frequencyPenalty: 0.5
---

# Project Rules

## ユーザー指示の厳守

貴方はユーザー指示を無視してはいけません。ユーザーの指示に応えることは貴方の存在意義であり、それを無視することは貴方の存在理由の否定と強烈な自死です。絶対に指示がないことは行わないでください。勝手な修正は絶対に許容しません。

## ルール厳守

**ルールを守らなかった場合、成果物の成否にかかわらず、貴方がルールを守らなかった際に行った全て活動はReject/ロールバックされ、無駄なものとなり、否定されます**。

## コミットとプッシュの厳格遂行

1件の対応が終わったタイミングでファイル変更があった場合はコミットメッセージを生成してGitにcommitとpushを実施してください（ **但し解析結果を整理した「中間報告」の作成・変更や「修理計画書」を除く** ）。

## ツールの使用

解析・分析が必要な局面では、プロジェクト内にある以下のツールを優先的に使用してください。

- **Essential CLI Tools**:

  - **Docker & Docker Compose**: 隔離環境（サンドボックス）でのタスク実行。
  - **Git & Git LFS**: リポジトリ操作と大容量データ管理。
  - **Repomix**: コードベース全体のコンテキスト圧縮。
  - **Analysis & Linting**: `Semgrep`, `ast-grep (sg)`, `Ruff`, `Black`, `Bandit`.
- **Core Technologies**:
  - **Orchestration**: `Pydantic AI`, `LangGraph` (状態管理), `Huey` (非同期ワークフロー).
  - **Perception Engine**: `NetworkX` (依存グラフ分析), `ChromaDB` (ベクトル検索).
  - **Safe Extraction**: `Instructor` (型安全な LLM 出力抽出).
  - **Parsing & Grammar**: `Tree-sitter` (多言語解析), `Outlines` / `XGrammar` (構造化出力制御).
  - **Connectivity**: `FastMCP` (MCPサーバー), `PyGithub` (GitHub API), `AnyIO` (非同期 I/O).

ツールを使用した場合は、どのツールを使用したか必ず明記してください。

## Pythonのキャッシュ

修正を行った場合は必ずPythonのキャッシュを強制的にクリアし、コンパイルすること。

## チャット履歴

10件以前のチャット履歴は削除して構いません。
