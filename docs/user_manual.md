# Brownie ユーザーマニュアル (User Manual)

BROWNIE は、AI エージェントが自律的にソフトウェア開発の全工程（調査・設計・実装・検証・PR作成）を完結させるためのエンジニアリング基盤です。このドキュメントでは、導入から日常的な使用方法までを解説します。

---

## 1. クイックスタート (Quick Start)

### 1.1 インストール
リポジトリをクローンし、セットアップスクリプトを実行します。これにより、仮想環境（`.venv`）の作成と必要な依存関係のインストールが自動的に行われます。

```bash
git clone https://github.com/globalpocket/brownie.git
cd brownie
./bin/setup.sh
```

### 1.2 環境設定
`.env` ファイルを作成し、以下の項目を設定してください。

```ini
GITHUB_TOKEN=your_personal_access_token
BROWNIE_LANGUAGE=ja
# 必要に応じて、LLM プロバイダーの設定を追加
```

### 1.3 起動
Orchestrator（司令塔）と Worker（実行役）を起動します。

```bash
./bin/brwn start
```

---

## 2. CLI の使い方 (Command Line Interface)

`bin/brwn` コマンドを使用して、システムの状態管理やログの確認を行います。

| コマンド | 内容 |
| :--- | :--- |
| `start` | システム（Orchestrator & Worker）をバックグラウンドで起動します。 |
| `stop` | 実行中のプロセスを安全に停止します。 |
| `status` | プロセスの稼働状況、PID、アクティブなタスク数を表示します。 |
| `logs` | 全コンポーネントの統合ログをリアルタイムで表示します（Tail）。 |
| `queue` | 現在キューに溜まっているタスクの状態を確認します。 |
| `reset` | 状態データベース（LangGraph のチェックポイント）を初期化します。 |

---

## 3. IDE 連携 (MCP Server としての利用)

Brownie のツール群（セマンティック検索、AST解析、サンドボックス実行など）は、MCP に対応した IDE（Cursor、VS Code、Claude Desktop など）から直接呼び出すことができます。

### 3.1 設定例 (Claude Desktop / Cursor)
IDE の MCP 設定（`config.json` 等）に以下のサーバーを追加することで、エージェントが提供する高度な解析機能を人間が直接利用できます。

**Knowledge Server (コード解析・検索):**
```json
"brownie-knowledge": {
  "command": "python",
  "args": [
    "-m", "src.mcp_server.knowledge_server",
    "/path/to/your/repo",
    "~/.local/share/brownie/memory",
    "your_repo_name"
  ],
  "env": {
    "PYTHONPATH": "/absolute/path/to/brownie"
  }
}
```

**Workspace Server (セキュアな書き換え・実行):**
```json
"brownie-workspace": {
  "command": "python",
  "args": [
    "-m", "src.mcp_server.workspace_server",
    "/path/to/your/repo",
    "/path/to/reference/code",
    "1000", "1000"
  ],
  "env": {
    "PYTHONPATH": "/absolute/path/to/brownie"
  }
}
```
*※注: `/path/to/...` 部分はお使いの環境の絶対パスに、`1000` は Linux/Mac のユーザー ID に置き換えてください。*

---

## 4. GitHub との連携ワークフロー

Brownie は主に GitHub の Issue をトリガーとして動作します。

### 4.1 タスクの投入方法
1.  **Issue の作成**: 管理対象のリポジトリで Issue を作成します。
2.  **ラベルの付与**: `brownie` ラベルを付与するか、Issue 内で `@brownie` をメンションします。
3.  **自動検知**: Orchestrator が Issue を検知し、自律的な解析を開始します。

### 4.2 AI との対話
*   **意図の確認**: 解析完了後、Brownie は Issue に「実行計画の提案」をコメントします。
*   **承認と指示**: ユーザーがコメントで「y」や「実行してください」と返信すると、実装フェーズに進みます。
*   **PR の作成**: 実装と検証（テスト）が完了すると、自動的に Pull Request が作成されます。

---

## 5. 知っておくべき主要コンポーネント

### 🛡️ サンドボックス (Sandbox)
コードの実行やテストは、すべて Docker コンテナ内で行われます。ホスト環境を壊す心配はありません。

### 💾 状態管理 (Persistence)
進行中のタスクは Redis および SQLite に保存されます。システムを再起動しても、タスクの途中から再開することが可能です。

### 🩺 自己診断 (Self-Healing)
環境の不整合やエラーを検知すると、エージェントは自動的に原因を分析し、可能な限り自律的に修復を試みます。

---

## 6. トラブルシューティング

### ログの確認
不具合が発生した場合は、まず統合ログを確認してください。
```bash
./bin/brwn logs
```
個別のコンポーネントのログは `logs/` ディレクトリ配下に格納されています。

### プロセスの強制クリーンアップ
正常に `stop` できない場合は、以下のスクリプトで関連プロセスを一掃できます。
```bash
./bin/unsetup.sh
```
*※注: この操作はモデルキャッシュ以外の環境を初期化するため、再起動には `./bin/setup.sh` が必要です。*

---
> 📅 **最終更新日**: 2026-04-20
> 🍪 **Enjoy coding with BROWNIE!**
