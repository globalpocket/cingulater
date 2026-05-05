# 🐻 Cingulater: Minimal Conversational Core

Cingulater は、信頼性と透明性を最優先した最小限の対話エンジンです。
肥大化した過去のアーキテクチャをすべて破棄し、LLM との確実な対話とプロンプト注入に特化して再構築されました。

## 🎯 Core Concept

1.  **Minimalism**: 不要な Manager や Workflow を排除し、Orchestrator を唯一の核とします。
2.  **Prompt-Driven**: すべての挙動は `.cingulater/system_prompt.md` によって統治されます。
3.  **Transparency**: AI の思考プロセスを隠蔽し、最終的な回答のみを誠実に出力します。

## 💻 Tech Stack

- **Logic**: Python 3.12 / Pydantic
- **API**: FastAPI (OpenAI 互換)
- **LLM Interface**: OpenAI API 互換クライアント

## 📂 Project Structure

-   `bin/cingulater`: エントリポイント
-   `src/core/orchestrator.py`: プロンプト注入・対話コア
-   `src/api/server.py`: API エンドポイント
-   `.cingulater/system_prompt.md`: システムプロンプト（憲法）

## 🚀 Usage

```bash
# サーバーの起動
./bin/cingulater
```

---
*Warning: このプロジェクトは現在、安定化のために最小構成までリセットされています。*
