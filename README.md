<div align="center">
 
![BROWNIE Banner](docs/images/banner.jpeg)

# 🍪 BROWNIE
### Your Friendly Autonomous Development Assistant

[![Status](https://img.shields.io/badge/status-active-success.svg)]()
[![License](https://img.shields.io/badge/license-MIT-blue.svg)]()
[![Version](https://img.shields.io/badge/version-0.1.0--alpha-orange.svg)]()
[![Powered By](https://img.shields.io/badge/powered%20by-Model%20Context%20Protocol-8A2BE2.svg)]()

**BROWNIE** は、AI エージェントが自律的にソフトウェア開発の全工程（調査・設計・実装・検証・PR作成）を完結させるために最適化された、次世代のエンジニアリング基盤です。

[Explore the Docs »](docs/Home.md)
/
[View Blueprints »](#-blueprints)
/
[Quick Start »](#-getting-started)

</div>

---

## 🌟 Why BROWNIE?

従来の開発環境は「人間」のために設計されてきました。しかし、AI エージェントが自律的に働くためには、より「構造的」で「堅牢」な基盤が必要です。BROWNIE は、AI が「迷わず、安全に、確実な成果を出すこと」に特化した **Agent-Friendly Architecture** を提供します。

| 🏎️ **High Locality** | 🎯 **Explicit Tools** | 🛡️ **Robust Infra** | 🧠 **Meta-Cognition** |
| :--- | :--- | :--- | :--- |
| パス解決とセキュリティ境界を一元化。 | 厳格な型定義でツールの誤用を防止。 | 独立したサーバー群による鉄壁のプロセス管理。 | 自己診断を行い不整合を自律修復。 |

#### 1. High Locality (境界の集約)
AI が直面する「複雑なパストラバーサルの恐怖」と「コンテキストの欠如」を構造的に解決しました。`WorkspaceContext` がすべてのパス解決とセキュリティ境界を一元管理。AI はリポジトリルートからの相対パスのみを意識すればよく、環境差異や絶対パスの不一致というノイズから完全に解放されます。

#### 2. Explicit Tools (明示的なコントラクト)
ツールの誤用やハルシネーションを極限まで抑制します。すべてのツールは、Pydantic AI を用いた厳格な型定義と、AI 向けの「設計意図」が記述された詳細な Docstring（契約）を持ち、エージェントは自らの「手」の機能を論理的に把握できます。

#### 3. Robust Infrastructure (堅牢なプロセス管理) & JIT Tool Loading
`MCPServerManager` による鉄壁のプロセス制御。各タスクごとに独立した コア MCP サーバー群を起動するだけでなく、必要な時だけ特定の解析ツールを起動・破棄する **JIT (Just-In-Time) ロード機構** を搭載。13種類の高度な静的解析・アーキテクチャ分析ツールがビルトインされており、不要なメモリ圧迫やLLMの分析麻痺（Analysis Paralysis）を防ぎます。

#### 4. Meta-Cognition (自己診断能力)
エージェントは自らの実行状態やコンテキストを客観的に把握する能力（`get_agent_context`）を持っています。実行エラー発生時には、まず自律的に自己診断を行い、プロジェクト環境の不整合を検知して修復するループ（Self-Healing）を回します。

---

## 🧠 Multi-Agent（マルチエージェント）アーキテクチャ

BROWNIE は、役割を分担させた複数の AI モデルを連携させる「Multi-Agent アーキテクチャ」を採用しています。これにより、単一の軽量モデルが抱えていた「Function Calling の不安定さ」と「コーディング能力の不足」というトレードオフを、構造的に解決しました。

---

## 🏗 システムアーキテクチャ (Overview)

BROWNIE は以下の統合されたコンポーネントで構成されています。

- **🧠 Orchestrator**: システムの司令塔。GitHub のポーリング、**Huey (Redis)** へのタスク投入、リソースの初期化、そして全体の状態管理を司ります。
- **🛡 SandboxManager**: Docker を基盤とした安全な実行環境。YAML サニタイザにより、特権実行や不正なマウントを構造的に遮断し、安全なコード実行を保証します。
- **💾 StateManager**: **LangGraph (SQLite Checkpointer)** を使用した高信頼な状態管理。OS クラッシュ時でもタスクの整合性を維持し、再起動後のリカバリーを可能にします。
- **🛠️ MCPServerManager**: 推論・知覚・実行を支える **「MCP サーバー群のライフサイクル管理と統合インターフェース」**を担当します。
- **🔌 MCP Servers**:
  - **🩺 Resource Monitor Server**: システムのメモリとCPUリソース、およびプロセスの状態を監視し、AI実行の安全性を判断・確保を担います。
  - **📋 Code Planner Server**: 設計担当。Pydantic AI エージェントがタスクを分析し、厳格な設計図 (Blueprint) を生成します。
  - **✍️ Code Writer Server**: 実装担当。Blueprint に基づき、決定論的に高品質なソースコードを生成します。
  - **📖 Knowledge Server**: AST 解析、RAG、シンボル検索を提供し、AI に「深いコード理解」を与えます。
  - **🏖️ Workspace Server**: サンドボックス内での安全なファイル操作、Git 操作、Linter 実行を担います。

---

## 📚 ドキュメント (Documentation)

BROWNIE の各モジュールに関する詳細な設計書は `docs/` ディレクトリに格納されています。

- **Blueprints**: AI がシステムを完全にリバースエンジニアリング・再構築するために最適化された「厳密な設計図」です。
  - [StateManager (LangGraph) 設計書](docs/src_core_graph_state.md)
  - [MCPServerManager 設計書](docs/src_mcp_server_manager.md)
  - [SandboxManager 設計書](docs/src_workspace_sandbox.md)
  - [Orchestrator 設計書](docs/src_core_orchestrator.md)

---

## 🏗️ Architecture Layers

BROWNIE は 3 つの分離されたプレーンで構成され、高い信頼性と拡張性を実現しています。

### 🧠 Control Plane
**The Brain.** LangGraph によるワークフロー制御と、Planner-Executor パターンによる高度な意思決定を行います。
- `Orchestrator` / `Agent` / `Workflow`

### 💾 Perception Plane
**The Eyes.** DuckDB による AST 解析と NetworkX による依存関係分析により、コードベースの「空間的」把握を支援します。
- `Knowledge MCP Server` / `Code Analyzer`

### 🛠️ Execution Plane
**The Hands.** Docker 隔離環境（Sandbox）内での副作用実行と、厳格な検証を担います。
- `Workspace MCP Server` / `Sandbox Manager`

---

## 💎 最大の特徴: Agent-Friendly Architecture

BROWNIE は、AI が「迷わず、壊さず、学び続ける」ための 4 つの柱を実装しています。

### 1. High Locality (境界の集約)
AI が直面する「複雑なパス解決」の問題を構造的に解決しました。`WorkspaceContext` がすべてのパス解決とセキュリティ境界を一元管理。AI はリポジトリルートからの相対パスのみを意識すればよく、ホスト環境の物理的な絶対パスというノイズから完全に解放されます。

### 2. Explicit Tools (明示的なコントラクト)
ツールの動的な生成や曖昧なディスパッチを廃止。**Pydantic AI** を採用し、厳格な型定義と詳細な Docstring を持つ明示的なツールセットを提供します。これにより、LLM の推論時における引数の取り違えやハルシネーションを極限まで抑制しています。

### 3. Robust Infrastructure (堅牢なプロセス管理)
**LangGraph** による状態遷移管理と **Huey (Redis-backed)** による非同期タスクキューイングを導入。`MCPServerManager` が各タスクごとに独立した MCP サーバー（Knowledge / Workspace）をライフサイクル管理し、ゾンビプロセスの発生を防ぎ、システムリソースの整合性を保ちます。

### 4. Meta-Cognition (自己診断能力)
エージェントは自らの実行状態やコンテキストを客観的に把握する (`get_agent_context`) ツールを持っています。エラー発生時には自己診断を行い、ワークスペースの不整合を自律的に検知・修正。AI 自身が「今何をしているか、何が起きたか」を正しく理解し、迷走を防止します。

---

## 💻 Tech Stack

BROWNIE は、最高峰の OSS ライブラリを組み合わせて構築されています。

| Layer | Technologies |
| :--- | :--- |
| **Logic & State** | ![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white) ![Pydantic](https://img.shields.io/badge/Pydantic-E92063?style=flat-square&logo=pydantic&logoColor=white) ![LangGraph](https://img.shields.io/badge/LangGraph-232F3E?style=flat-square) ![Huey](https://img.shields.io/badge/Huey-Red?style=flat-square) |
| **Connectivity** | ![FastMCP](https://img.shields.io/badge/FastMCP-blue?style=flat-square) ![GitHub](https://img.shields.io/badge/GitHub-181717?style=flat-square&logo=github&logoColor=white) ![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white) |
| **Intelligence** | ![Tree-sitter](https://img.shields.io/badge/Tree--sitter-black?style=flat-square) ![DuckDB](https://img.shields.io/badge/DuckDB-FFF000?style=flat-square&logo=duckdb&logoColor=black) ![ChromaDB](https://img.shields.io/badge/ChromaDB-lightgrey?style=flat-square) |
| **Analysis** | ![Semgrep](https://img.shields.io/badge/Semgrep-blue?style=flat-square) ![Ruff](https://img.shields.io/badge/Ruff-orange?style=flat-square) ![Black](https://img.shields.io/badge/Black-000000?style=flat-square) |

---

## 📚 Blueprints

BROWNIE は、AI 自身がシステムを理解・再構築できるレベルの「厳格な設計書」として存在します。

| Category | Components |
| :--- | :--- |
| **Core** | [Orchestrator](docs/src_core_orchestrator.md) • [Agent](docs/src_core_agent.md) • [Workflow](docs/src_core_workflow.md) |
| **Workspace** | [Context](docs/src_workspace_context.md) • [Sandbox](docs/src_workspace_sandbox.md) • [GitOps](docs/src_workspace_git_ops.md) |
| **Analysis** | [Analyzer](docs/src_workspace_analyzer_core.md) • [FlowTracer](docs/src_workspace_analyzer_flow.md) • [Repomix](docs/src_workspace_repomix_runner.md) |
| **Infra** | [Manager](docs/src_mcp_server_manager.md) • [WorkspaceServer](docs/src_mcp_server_workspace_server.md) • [KnowledgeServer](docs/src_mcp_server_knowledge_server.md) |

---

## 🚀 Getting Started

### 📋 Prerequisites

BROWNIE のフル機能を活用するには、以下の環境とプログラムが必要です。これらの依存関係の多くは `./bin/setup.sh` によって自動的にインストール・設定され、`./bin/unsetup.sh` によってシステムから安全にクリーンアップされます。

- **Hardware & OS**:
    - **Apple Silicon (M1/M2/M3)**: 高速なローカル推論 (MLX) のための推奨環境。
    - **macOS / Linux**: 推奨ランタイム環境。
- **Language Runtimes & Managers**:
    - **Python 3.11+**: メインランタイム。
    - **[uv](https://github.com/astral-sh/uv)**: 高速なパッケージ・プロジェクト管理に使用。
    - **Node.js / npm**: JavaScript/TypeScript の静的解析 (`ESLint`, `Prettier`) に必要。
- **LLM Models & Providers**:

    planner: "mlx-community/gemma-4-26b-a4b-it-4bit"
    executor: "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"


    - **Local**: `mlx-community/gemma-4-26b-a4b-it-4bit` (Planner), `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` (Executor).
    - **Cloud**: `Google Gemini` (デフォルトのバリデーションエンジン), OpenAI, Anthropic 等。
    - *※ [LiteLLM](https://github.com/BerriAI/litellm) によるマルチプロバイダー対応。*
- **Essential CLI Tools**:
    - **Docker & Docker Compose**: 隔離環境（サンドボックス）でのタスク実行。
    - **Git & Git LFS**: リポジトリ操作と大容量データ管理。
    - **Repomix**: コードベース全体のコンテキスト圧縮。
    - **Analysis & Linting**: `Semgrep`, `ast-grep (sg)`, `Ruff`, `Black`, `Bandit`.
- **Core Technologies**:
    - **Orchestration**: `Pydantic AI`, `LangGraph` (状態管理), `Huey` (非同期ワークフロー).
    - **Perception Engine**: `DuckDB` (AST解析), `NetworkX` (依存グラフ分析), `ChromaDB` (ベクトル検索).
    - **Safe Extraction**: `Instructor` (型安全な LLM 出力抽出).
    - **Parsing & Grammar**: `Tree-sitter` (多言語解析), `Outlines` / `XGrammar` (構造化出力制御).
    - **Connectivity**: `FastMCP` (MCPサーバー), `PyGithub` (GitHub API), `AnyIO` (非同期 I/O).

### 🔧 Installation
```bash
# クローンとセットアップ
git clone https://github.com/globalpocket/brownie.git
cd brownie
./bin/setup.sh
```

### 🏃 Running
```bash
# Orchestrator と Worker の起動
./bin/brwn start
```

---

## 🔬 Technical Deep Dive

### 🗄️ AIモデルの管理：HuggingFaceの「デフォルトの罠」とBrownieの対策

Brownieはローカル環境で強力なAIを稼働させるため、数十GBに及ぶ巨大なLLM（大規模モデル）をダウンロードします。このモデルファイルの管理において、BrownieはHuggingFaceのデフォルト挙動が抱えるリスクを回避する独自の安全設計を採用しています。

#### 🚨 HuggingFaceの「デフォルトの罠」
通常、HuggingFaceのライブラリは「インストール不要ですぐにモデルを試せる」ことを優先し、モデルをOS標準の一時保管場所（`~/.cache/huggingface/hub/`）に保存します。
しかし、15GBを超えるような「再ダウンロードに膨大な時間とネットワークリソースを要するデータ」を、OSの都合で消去されうる「一時キャッシュ」として扱うことは、巨大なローカルLLMを運用する上で大きなリスク（設計上の脆弱性）となります。

#### 🛡️ Brownieの解決策：キャッシュから「大切な資産」へ
Brownieは、この危ういデフォルト挙動にあえて従いません。
システムとスクリプトレベルで保存先を明示的に上書きし、**Brownie専用の安全な永続データ領域**へとモデルを隔離します。

* **専用の保存場所:** `~/.local/share/brownie/models/`
* **設定のカスタマイズ:** `config/config.yaml` の `model_dir` にて、ユーザーの環境に合わせて柔軟に変更可能です。

これにより、Brownieは巨大なAIモデルを単なる「キャッシュ（一時的なゴミ）」ではなく、システムの中核を成す**「大切な資産（アセット）」**として保護します。OSのクリーンアップ等による不意の消失を防ぎ、安定したローカル開発環境を約束します。

#### 💾 ディスク容量の解放（不要な過去モデルの削除）について
Brownieは上記のようにモデルを大切に保管するため、設定（`config.yaml`）でAIモデルを別のモデルに切り替えて試行した場合でも、過去にダウンロードした古いモデルデータは自動的には削除されません。そのため、過去の試行錯誤の跡が蓄積し、ディスク容量を数十GB圧迫する場合があります。

容量を解放するには、`bin/unsetup.sh` を実行してクリーンアップを行うか、`~/.local/share/brownie/models/` 内の不要なモデルディレクトリを手動で削除してください。

### 🛡️ Secure Sandbox
すべてのコード実行と検証は、`SandboxManager` が制御する Docker コンテナ内で行われます。ホストマシンのファイルシステムやネットワークへの不用意な干渉は構造的に遮断されており、AI エージェントが自律的に `rm -rf /` を実行しても安全です。

---

<div align="center">

### 🤝 Join the Autonomous Revolution
BROWNIE は、AI が「ただの道具」ではなく「自律的なチームメンバー」として機能するための、最も信頼できる基盤を提供します。

[GitHub](https://github.com/globalpocket/brownie) / [Wiki](docs/Home.md)

</div>
