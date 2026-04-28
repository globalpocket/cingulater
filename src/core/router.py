import httpx
from loguru import logger
from core.config import Settings

class Router:
    """
    BROWNIE Router: 入力プロンプトに対して最適な担当モデルを選択する。
    LLMによるゼロショット分類（およびヒューリスティック）を使用し、
    巨大な機械学習モデル（torch等）への依存を排除した超軽量版。
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.endpoint = settings.llm.interlocutor_endpoint
        self.model_name = settings.llm.models.get("interlocutor", "default")
        self.timeout = settings.llm.timeout_sec
        
        # 高速判定用のキーワード（これらが含まれていれば LLM に聞かず即 Coder へ）
        self.coder_keywords = [
            "コード", "修正", "実装", "バグ", "エラー", "リファクタ",
            "スクリプト", "ファイル", "プログラム", "作って", "追加して"
        ]
        logger.info("Lightweight LLM Router initialized.")

    async def route(self, query: str) -> str:
        """
        クエリに対して最適なモデルラベル('coder' または 'interlocutor')を返す。
        """
        if not query:
            return "interlocutor"

        # 1. ヒューリスティック (高速・一次判定)
        for kw in self.coder_keywords:
            if kw in query:
                logger.debug(f"Router: Keyword match '{kw}' -> coder")
                return "coder"

        # 2. LLM によるゼロショット判定 (フォールバック)
        logger.debug("Router: Falling back to LLM classification...")
        prompt = (
            "Classify the following user input into one of two categories:\n"
            "1. 'coder' (requires writing/modifying code, file operations, debugging)\n"
            "2. 'interlocutor' (general conversation, greetings, simple questions)\n\n"
            "Output ONLY the category name ('coder' or 'interlocutor'). No other text.\n\n"
            f"Input: {query}"
        )

        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 10,
            "temperature": 0.0
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.endpoint}/chat/completions", json=payload)
                resp.raise_for_status()
                result = resp.json()
                answer = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip().lower()
                
                if "coder" in answer:
                    logger.debug("Router: LLM decided -> coder")
                    return "coder"
                else:
                    logger.debug("Router: LLM decided -> interlocutor")
                    return "interlocutor"
        except Exception as e:
            logger.error(f"Router LLM Error: {e}. Defaulting to interlocutor.")
            return "interlocutor"