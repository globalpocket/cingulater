---
description: Brownie のコアモジュールを読み解き、AI 最適化された設計書（Blueprint）の生成と更新を行います。
---

このワークフローは、システムの主要コンポーネントをリバースエンジニアリングし、AI が理解しやすい「Agent-Friendly Architecture」に基づいたドキュメントを生成・更新します。

### ステップ 1: 設計書 (Blueprint) の生成

`src/` 以下の各ファイルについて、AI がシステムを復元できるレベルの詳細な設計書を作成し、`docs/src_ディレクトリ名_ファイル名.md` として出力してください。

#### Blueprint 出力ルール
- **1ソースファイルにつき、1つのMarkdownファイル**を出力する。
- ファイル命名規則: `docs/src_ディレクトリ名_ファイル名.md`（例: `docs/src_core_state.md`）。
- **AI 向けの厳密なコントラクト**（引数の型、必須の振る舞い、例外発生条件、副作用）を記述する。
- 以下のフォーマットテンプレートに従うこと。

> **フォーマットテンプレート:**
> # Blueprint: `src/path/to/file.py`
> ## 1. 責務 (Responsibility)
> [モジュールの役割、設計思想、解決する課題を簡潔に記述]
> ## 2. 復元要件 (Recreation Requirements for AI)
> ### クラス: `ClassName`
> **初期化引数:**
> - `arg_name` (Type): Description
> **公開メソッド:**
> 1. `method_name(args) -> ReturnType`
>    - **入力**: 引数の説明
>    - **振る舞い**: 内部ロジック、チェック項目、状態変化
>    - **例外発生**: 特定の条件下で投げられる例外
>    - **出力**: 戻り値の説明
> ## 3. 依存関係 (Dependencies)
> - 標準ライブラリ: ...
> - 外部依存: ...

---

### ステップ 2: Home.md (システム概要) の更新

各 Blueprint を包含するシステム全体の全体図である `docs/Home.md` を更新してください。

#### 盛り込むべき内容
1.  **アーキテクチャ原則 (Architecture Principles)**: 「Agent-Friendly」な設計思想の説明。
2.  **主要コンポーネント詳細 (Component Breakdown)**: Control Plane (Orchestrator/Agent), Perception Plane (State/Knowledge), Execution Plane (Sandbox) の役割。
3.  **シーケンス図 (Core Sequences)**: タスクの検知から完了までの主要な流れ (mermaid)。
4.  **運用・自己修復 (Operations)**: Watchdog や Resume 機能の説明。

#### 出力ルール
- Blueprint レベルの詳細に入りすぎず、システム全体の「地図」としての役割を維持する。
- `docs/` 内の各 Blueprint へのリンクを適切に配置する。