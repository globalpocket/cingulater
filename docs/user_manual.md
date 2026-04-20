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

## 3. 外部 AI エージェントとの連携 (Antigravity / MCP)

Brownie は、他の高度な AI エージェント（Antigravity や Claude Desktop など）に「専門的な開発機能」を貸し出すための **Brownie Agent Server** を提供しています。

### 3.1 設定方法 (Antigravity 例)
ユーザー設定の `mcp_config.json` に以下のサーバー定義を追加します。

```json
{
  "mcpServers": {
    "brownie": {
      "command": "/absolute/path/to/brownie/.venv/bin/python",
      "args": ["-m", "src.mcp_server.brownie_agent_server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/brownie",
        "REDIS_HOST": "localhost"
      }
    }
  }
}
```
*※注: パスはご自身の環境の絶対パスに置き換えてください。*

### 3.2 使用例 (Usage Scenarios)

Antigravity などのエージェントに対し、以下のように指示を出すことで Brownie を操作できます。

1. **修正タスクの依頼**:
   > 「リポジトリ `globalpocket/brownie` の Issue #123 の修正を Brownie に依頼して。」
   > (エージェントが `submit_task` を呼び出し、Brownie が自律作業を開始します)

2. **進捗状況の確認**:
   > 「Brownie に依頼した `brownie#123` の現在のステータスを教えて。」
   > (エージェントが `get_task_status` を呼び出し、現在のフェーズや実行計画を表示します)

3. **セキュリティスキャンの代行**:
   > 「このリポジトリの脆弱性調査を Brownie に投げておいて。Issue 番号は #456 だよ。」
   > (エージェントが Brownie にタスクを委譲し、人間は他の作業を継続できます)

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

---
> 📅 **最終更新日**: 2026-04-20
> 🍪 **Enjoy coding with BROWNIE!**
