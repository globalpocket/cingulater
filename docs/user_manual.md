# Brownie User Manual (Resident Driver Edition)

BROWNIE は、一度起動すれば常駐し続ける **「Resident Driver」** アーキテクチャを採用した自律型 AI エンジンです。

## 1. 使い方

### 対話を開始する (Chat Mode)
ターミナルで以下のコマンドを打つだけです。エンジンが動いていなければ自動的にバックグラウンドで起動します。
```bash
bin/brwn
```
*一度起動すれば、ターミナルを閉じても Brownie（エンジン）は裏側でポート 8137 で常駐し続けます。*

### 直接指示を与える (Direct Prompt)
対話モードに入らず、一行で指示を出すことも可能です。
```bash
bin/brwn "src/main.py のリファクタリング案を出して"
bin/brwn こんにちは
```

## 2. エンジンの管理

Brownie エンジン（知能の本体）は自動的に管理されますが、手動で操作することも可能です。

| コマンド | 役割 |
| :--- | :--- |
| `bin/brwn` | 対話モードの開始（未起動ならエンジン自動起動） |
| `bin/brwn start` | エンジンのみをバックグラウンドで起動する |
| `bin/brwn stop` | 常駐しているエンジンを完全に停止する |
| `bin/brwn status` | エンジンの稼働状況を確認する |

## 3. IDE 連携
常駐している Brownie は OpenAI 互換 API を提供しているため、IDE (VS Code 等) からも同時に接続可能です。

- **Endpoint**: `http://localhost:8137/v1`
- **Model**: `brownie-v2`

## 4. ログの確認
エンジンの詳細な動作ログは、以下で確認できます。
```bash
tail -f logs/engine.log
```
