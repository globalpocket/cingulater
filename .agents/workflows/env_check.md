---
description: 解析環境の構成診断 (Tree-sitter 文法チェック)
---

BROWNIE がソースコードを解析するために必要な構文解析エンジン (Tree-sitter) の健全性を診断してください。

以下のチェックを順次実行し、不備がある場合はユーザーに報告して修正案を提示してください。

## 1. Tree-sitter 基本パッケージの確認
// turbo
以下のコマンドでパッケージの存在を確認してください。
`./.venv/bin/python3 -c "import tree_sitter; print(f'tree-sitter version: {tree_sitter.__version__}')"`

## 2. 各言語の文法ロードテスト
解析に使用する主要言語の文法が正しくロードできるか、ワンライナーで検証してください。

### Python
// turbo
`./.venv/bin/python3 -c "from tree_sitter import Language; import tree_sitter_python; lang = Language(tree_sitter_python.language()); print('Python: Ready')"`

### JavaScript / TypeScript
// turbo
`./.venv/bin/python3 -c "from tree_sitter import Language; import tree_sitter_javascript; import tree_sitter_typescript; Language(tree_sitter_javascript.language()); Language(tree_sitter_typescript.language_typescript()); print('JS/TS: Ready')"`

### Go
// turbo
`./.venv/bin/python3 -c "from tree_sitter import Language; import tree_sitter_go; lang = Language(tree_sitter_go.language()); print('Go: Ready')"`

## 3. 診断結果の報告
すべてのロードに成功した場合は「Ready」と報告し、失敗したものについては再インストールコマンドなどの解決策を提示してください。
