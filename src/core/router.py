import torch
from sentence_transformers import CrossEncoder
from loguru import logger
from typing import List, Dict

class Router:
    """
    BROWNIE Router: 入力プロンプトに対して最適な担当モデルを選択する。
    BAAI/bge-reranker-v2-m3 を使用した Cross-Encoder 判定を行う。
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        logger.info(f"Loading Router model: {model_name}...")
        # Apple Silicon (mps) または CUDA が利用可能な場合は使用する
        device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        self.model = CrossEncoder(model_name, device=device)
        
        # 判定用の候補ラベルと説明文
        self.candidates = [
            {
                "label": "interlocutor",
                "description": "日常会話、挨拶、一般的な質問、情報の要約、感情的な応答、雑談。"
            },
            {
                "label": "coder",
                "description": "コードの修正、プログラムの作成、バグの修正、リファクタリング、ファイルの書き換え、実装、プログラミング指示。"
            }
        ]
        logger.info(f"Router loaded on {device}.")

    def route(self, query: str) -> str:
        """
        クエリに対して最適なモデルラベルを返す。
        相対スコアが最も高いものを選択する。
        """
        # クエリと各説明文のペアを作成
        pairs = [[query, c["description"]] for c in self.candidates]
        
        # スコアリング
        scores = self.model.predict(pairs)
        
        # 結果の解析
        results = []
        for i, score in enumerate(scores):
            results.append({
                "label": self.candidates[i]["label"],
                "score": float(score)
            })
            logger.debug(f"Router Score - {self.candidates[i]['label']}: {score:.4f}")

        # 最も高いスコアを選択
        best_match = max(results, key=lambda x: x["score"])
        
        logger.info(f"Routing query to: {best_match['label']} (score: {best_match['score']:.4f})")
        return best_match["label"]

if __name__ == "__main__":
    # 単体テスト用
    router = Router()
    print(f"Test 1: {router.route('こんにちは')}")
    print(f"Test 2: {router.route('src/main.py を修正して')}")
