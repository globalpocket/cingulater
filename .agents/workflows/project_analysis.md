# Project Analysis Workflow

このワークフローは、BROWNIE プロジェクトの状態を詳細に分析し、AI エージェントが現在の実装状況、コード品質、およびセキュリティ上の懸念を把握するために使用します。

## Steps

### 1. 環境の準備
- Docker デーモンが起動していることを確認します。
- 必要に応じて `./bin/setup.sh` が実行済みであることを確認します。

### 2. 分析スクリプトの実行
`.agents/scripts/agent_analyzer.py` を実行します。

```bash
uv run python .agents/scripts/agent_analyzer.py
```

### 3. レポートの確認
`docs/analysis/` ディレクトリに生成された最新の `AGENT_REPORT_*.md` を読み込み、以下の点を確認します：
- **致命的なエラー**: Semgrep や Bandit で検出された高優先度の問題。
- **コードの乱れ**: Ruff で検出されたリファクタリング推奨箇所。
- **全体構造**: Repomix で生成された集約コンテキスト。

### 4. まとめと提案
分析結果に基づき、以下のまとめを作成します：
- [ ] 現状の健全性（Health Score）
- [ ] 直ちに修正すべき箇所のリスト
- [ ] 長期的な改善提案（設計変更等）
