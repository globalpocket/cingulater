# Brownie Architecture V2: Detailed Interaction Model

BROWNIE は、プラットフォーム固有のロジック（GitHub 等）を実行層 (MCP) へ完全に分離したことで、コア知能の汎用化と安全性を高めました。以下に、刷新された対話フローの詳細を記述します。

---

## 1. 刷新された HITL (Human-In-The-Loop) フロー

以前の構成とは異なり、GitHub へのアクセスはすべて **Execution Plane (MCP Layer)** を介して行われます。

```mermaid
sequenceDiagram
    participant AGT as Core: Agent
    participant BRG as Core: Infra Bridge
    participant GH as MCP: GitHub Platform
    participant API as External: GitHub API
    participant USR as Human (Reviewer)
    
    Note over AGT: 実装フェーズ完了
    AGT->>BRG: post_comment(報告依頼)
    
    BRG->>GH: call_tool("post_comment", ...)
    Note right of GH: GitHub API 経由での投稿処理
    GH->>API: POST /issues/comments
    API-->>USR: 通知
    
    Note over USR: 内容を確認し、「/approve」と返信
    USR->>API: Comment: "/approve"
    
    loop 定期監視 (Polling Task)
        GH->>API: GET /notifications
        API-->>GH: "/approve" を検知
    end
    
    GH-->>BRG: 承認の検知イベント
    BRG-->>AGT: ワークフロー再開 (Resume)
    
    Note over AGT: Pull Request 作成へ
    AGT->>BRG: create_pull_request()
    BRG->>GH: call_tool("create_pull_request")
    GH->>API: POST /pulls
```

---

## 2. リポジトリ・プロビジョニング・フロー

リポジトリのクローンや同期も、コアが直接 git コマンドを叩くのではなく、MCP 経由で抽象化された要求として処理されます。

```mermaid
sequenceDiagram
    participant ORCH as Orchestrator
    participant BRG as Infra Bridge
    participant RMCP as MCP: Repo Provision
    participant GMCP as MCP: Official Git
    participant FS as Local File System
    
    ORCH->>BRG: ensure_repo_cloned(repo_name)
    BRG->>RMCP: call_tool("provision_repository")
    
    Note over RMCP: Git 認証情報の管理
    RMCP->>GMCP: call_tool("git_clone", auth_url)
    
    GMCP->>FS: git clone / checkout
    FS-->>GMCP: 完了
    GMCP-->>RMCP: 成功
    RMCP-->>BRG: プロビジョニング完了
    BRG-->>ORCH: 準備完了 (Workflow Start)
```

---

## 3. コンポーネント間コントラクト

以前の `Home.md` で定義された 3-Plane 構造を維持しつつ、接続インターフェースを MCP に一本化しました。

| コンポーネント | 以前の状態 (V1) | 現在の状態 (V2) |
| :--- | :--- | :--- |
| **Orchestrator** | GitHub API を直接操作 | **純粋な状態管理のみ** (Bridge 経由) |
| **Agent** | GitHub ラッパーに依存 | **プラットフォーム非依存** (Bridge 経由) |
| **GitHub Logic** | `src/gh_platform_client.py` | **`GitHub Platform MCP`** (分離) |
| **通信プロトコル** | Direct Python Call | **MCP (stdio/JSON-RPC)** |

---

この構造により、コアは「誰と喋っているか」を意識せず、`Infrastructure Bridge` という一貫した神経節を通じて、外の世界（GitHub, Git, Sandbox 等）を操ることが可能になっています。
