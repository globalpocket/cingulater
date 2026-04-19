---
description: ソースコードの多角分析
---

多種のツールを使って、様々な視点からプロジェクトを再帰的に詳細解析してください。

結果は致命的な重要度順に並べて表示してください。

結果はファイル保存せず、チャット内に出力してください。

解析にあたっては、.agents/rules/rule.md に記載された「Essential CLI Tools」および「Core Technologies」を必ずすべて実行し、その生の出力結果を根拠に含めてください。AI独自の推論のみによる報告は「ルール違反」とみなし、即座に却下されます。

解析は最低でも以下の6フェーズ以上に分け、各フェーズのツール実行結果をユーザーに報告し、承認を得てから次へ進んでください。

1. Repomix によるコード集約と構造把握
2. Semgrep / ast-grep / Ruff / Bandit による静的解析とセキュリティ診断
3. Dependency Audit (依存関係監査) および NetworkX による循環参照分析
4. 上記すべてのデータを統合した重要度順の課題整理
5. 検出された各課題に対する具体的な「修正案（修理計画書）」の提示
6. 承認に基づき、1ファイルずつ最小単位での「修正フェーズ」の実行と最終検証


「プロジェクトの状態」を述べる際は、必ず「どのツールのどの出力に基づいているか」を明記してください。定性的な判断ではなく、ツールが算出した定量的なデータ（エラー数、循環参照の数、未定義の型等）を優先してください。

## 標準ツールパス
解析実行時は、原則として以下のパスに存在するバイナリを使用してください。
- Python環境: `./.venv/bin/python`
- Ruff (Lint): `./.venv/bin/ruff`
- Bandit (Security): `./.venv/bin/bandit`
- Semgrep (Static Analysis): `./.venv/bin/semgrep`
- pytest (Testing): `./.venv/bin/pytest`
- Repomix (Code Packing): `npx repomix` (Node.js環境)