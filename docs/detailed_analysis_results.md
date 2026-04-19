# Brownie 深層解析診断レポート (2026-04-19)

## 1. 品質解析結果 (Ruff)
合計 **59 件** のエラーを検出。

- **Undefined names (F821)**: 型ヒントや変数のインポート漏れ。
- **Redefinition of unused 'logger' (F811)**: ロガーの二重定義。
- **Line too long (E501)**: 88 文字制限超過。

## 2. セキュリティ解析結果 (Bandit)
中〜高重要度の脆弱性を **8 件** 特定。

- **B602/B604: subprocess call with shell=True**: シェルインジェクションのリスク。
- **B307: eval() usage**: 動的な式評価の危険性。
- **B108: Hardcoded /tmp directory**: 一時ファイルパスの固定。
