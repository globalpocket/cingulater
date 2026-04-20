# Brownie Architecture V2: Functional Block Definition

## 1. システム全体構造
Brownie V2 は、制御層（Core）と実行層（MCP）を物理的・論理的に分離したアーキテクチャを採用しています。これにより、特定のプラットフォームに対する非依存性を担保しています。

## 2. 機能ブロック図

| レイヤー | コンポーネント | 役割・責務 | 依存関係 |
| :--- | :--- | :--- | :--- |
| **Input / UI** | GitHub / CLI / Slack | ユーザー指示の受容、進捗・結果の出力 | Core 層へ接続 |
| **Control (Core)** | **Orchestrator** | タスクの状態管理、実行ワークフローの制御 | Infrastructure Bridge |
| | **CoderAgent** | LLM を用いた思考・意思決定の実行 | Infrastructure Bridge |
| **Interface** | **Infrastructure Bridge** | Core の要求を MCP ツール呼び出しへ変換（絶縁体） | 各 MCP サーバー |
| **Execution (MCP)** | **GitHub Platform MCP** | GitHub API 通信（コメント投稿、Issue 取得等） | GitHub API (GhApi) |
| | **Git MCP** | ローカルリポジトリに対する Git 操作 | Local File System |
| | **Reasoning MCP** | 分離された環境での推論ループの実行 | LLM Endpoint |

## 3. 通信フロー

1.  **指示の入出力**:
    - GitHub (Issue) からの入力 → GitHub Platform MCP → Core
    - Core からの出力 → Infrastructure Bridge → GitHub Platform MCP → GitHub (Comment)
2.  **リポジトリ操作**:
    - Core → Infrastructure Bridge → Git MCP → ローカルクローン/コミット
3.  **推論実行**:
    - Core → Infrastructure Bridge → Reasoning MCP → 思考ループ実行

## 4. 設計パラダイム
- **Platform Agnostic**: Core レイヤーには GitHub 等のインポートが一切存在しません。すべての外部プラットフォームは「ツール」として扱われます。
- **Structural Integrity**: 依存関係は上位から下位への一方通行（Core -> Bridge -> MCP）であり、循環参照を完全に排除しています。
- **Security by Design**: ホスト環境へのアクセス権限は各 MCP サーバー単位で最小化されており、Core 自体は強力な特権を持ちません。
