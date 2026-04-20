# Brownie User Manual (V2)

BROWNIE は、プラットフォーム非依存の自律型 AI 開発エンジンです。ターミナル、IDE、GitHub など、あらゆる場所から「標準の口（OpenAI API）」を通じて対話することができます。

## 1. クイックスタート

Brownie を使用するには、まず「エンジン（脳）」を起動し、次に「クライアント（口）」を通じて指示を出します。

### Step 1: エンジンの起動
Brownie の推論コアをサーバーとして起動します。
```bash
bin/brwn engine
```
*注: デフォルトで `http://localhost:8000` で待機します。*

### Step 2: 対話の開始
別のターミナルから、対話型チャットを起動します。
```bash
bin/brwn chat
```

## 2. コマンドリファレンス

| コマンド | 役割 | 備考 |
| :--- | :--- | :--- |
| `bin/brwn engine` | 知能サーバーの起動 | OpenAI 互換 API を提供します。 |
| `bin/brwn chat` | 対話型チャットの開始 | ターミナルで Brownie と壁打ちできます。 |
| `bin/brwn "<指示>"` | 単発タスクの実行 | コマンドラインから直接命令を下します。 |
| `bin/brwn start` | GitHub 監視モード (Legacy) | GitHub の Issue メンションを監視します。 |
| `bin/brwn status` | システムの状態確認 | エンジンの稼働状況を表示します。 |

## 3. IDE との連携
Brownie は OpenAI API 規格に準拠しているため、既存の AI ツールから呼び出すことが可能です。

- **Endpoint**: `http://localhost:8000/v1`
- **Model**: `brownie-v2`
- **API Key**: (任意)

例: VS Code の Continue 等の `models` 設定に上記を追加することで、IDE から直接 Brownie の自律修正能力を利用できます。

## 4. 運用上の注意
- **GitHub 操作**: GitHub 関連の指示を出す場合は、`.env` に `GITHUB_TOKEN` が設定されていることを確認してください。
- **リポジトリ**: Brownie は指示されたコンテキスト（カレントディレクトリ等）に基づいて自律的に行動します。
