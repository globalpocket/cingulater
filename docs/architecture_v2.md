# Brownie Architecture V2: Multi-Platform Input Interaction

BROWNIE は、あらゆる入力ソース（GitHub, IDE, CLI）から同一の推論品質を提供するプラットフォーム非依存の設計を採用しています。以下に、主要な入力ルートごとのシーケンスを詳述します。

---

## 1. IDE 連携フロー (IDE -> Brownie MCP Server)

Brownie 自身が MCP サーバーとして振る舞い、外部の IDE AI エージェント（Cline, Roo Code 等）に思考能力を提供する最も強力な連携モードです。

```mermaid
sequenceDiagram
    participant IDE as External IDE (AI Agent)
    participant BMCP as Brownie MCP Server
    participant ORCH as Core: Orchestrator
    participant AGT as Core: Agent
    participant BRG as Core: Infra Bridge
    participant WS as MCP: Workspace / Git
    
    Note over IDE: ユーザーから「バグを直せ」と指示
    IDE->>BMCP: call_tool("submit_task", {issue_desc})
    
    BMCP->>ORCH: inject_task(payload)
    ORCH->>AGT: start_reasoning()
    
    loop 自律推論・修正
        AGT->>BRG: 操作要求
        BRG->>WS: 実ファイル操作・テスト実行
        WS-->>AGT: 実行結果の返却
    end
    
    AGT-->>ORCH: ソリューションの提示
    ORCH-->>BMCP: タスク完了報告
    BMCP-->>IDE: ToolResult (修正完了サマリ)
    Note over IDE: ユーザーに結果を表示
```

---

## 2. GitHub 連携フロー (GitHub -> Webhook/Polling)

GitHub 上の Issue や PR コメントをトリガーに、非同期で自律修正を行う、開発自動化の中核フローです。

```mermaid
sequenceDiagram
    participant API as External: GitHub API
    participant GH as MCP: GitHub Platform
    participant ORCH as Core: Orchestrator
    participant BRG as Core: Infra Bridge
    
    loop ポーリング (Poll Mentions Task)
        GH->>API: 新着通知の確認
        API-->>GH: Issue メンションを検知
    end
    
    GH->>ORCH: dispatch_event("on_github_mention")
    
    Note over ORCH: ワークフロー起動
    ORCH->>BRG: post_comment("解析を開始します")
    BRG->>GH: call_tool("post_comment")
    GH->>API: POST /comments
```

---

## 3. CLI 直接操作フロー (CLI -> Local Entrypoint)

開発者がローカル環境で直接コマンドを叩き、Brownie をスタンドアロンのツールとして使用する高速開発フローです。

```mermaid
sequenceDiagram
    participant DEV as Developer (Terminal)
    participant CLI as src.main: CLI
    participant ORCH as Core: Orchestrator
    participant AGT as Core: Agent
    
    DEV->>CLI: brownie run "この関数の警告を消して"
    CLI->>ORCH: create_immediate_task()
    
    Note over ORCH: ローカル権限で実行
    ORCH->>AGT: reasoning_loop()
    
    AGT-->>DEV: Terminal Output: "修正が完了しました。"
```

---

## 4. 全プラットフォーム共通の接続構造

すべての入力ソースは、最終的に `Orchestrator` へとタスクを投入し、同一の `Agent` と `Infra Bridge` を共有します。

| 入力ソース | Brownie の役割 | トリガー | 成果物の届け先 |
| :--- | :--- | :--- | :--- |
| **GitHub** | 自律保守エージェント | コメント / メンション | GitHub Comment / PR |
| **IDE (VS Code等)** | 思考 MCP プロバイダー | IDE 経由のツール呼び出し | IDE エディタ上の反映 |
| **CLI** | スタンドアロン・アシスタント | コマンド実行 | 標準出力 / ローカルファイル |
| **Slack / Email** | プロンプト・インターフェース | メッセージ受信 | 返信メッセージ |

---

このマルチ・インターフェース設計により、Brownie はコンテキスト（どこで呼ばれたか）に縛られず、常に一貫した開発能力を提供します。
