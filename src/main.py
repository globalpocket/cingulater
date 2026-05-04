import os
import sys
import typer
import uvicorn
from loguru import logger
from typing import Optional
from typing_extensions import Annotated
from dotenv import load_dotenv

# .env ファイルの読み込み
load_dotenv()

# プロジェクトルートをパスに追加
sys.path.append(os.path.dirname(__file__))

def setup_logging():
    log_level = "DEBUG" if os.environ.get("CINGULATER_DEBUG") == "1" else "INFO"
    logger.remove()
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    logger.add(sys.stderr, level=log_level, format=log_format)

def main(
    host: Annotated[str, typer.Option("--host", "-h", help="Host to bind")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port", "-p", help="Port to bind")] = 8137,
    config: Annotated[Optional[str], typer.Option("--config", "-c", help="Path to config yaml file")] = None,
):
    """Cingulater: AI Backend Core 🚀"""
    setup_logging()
    
    if config:
        os.environ["CINGULATER_CONFIG"] = config
        
    logger.info(f"Starting Cingulater API server on http://{host}:{port}")
    
    # API サーバーを起動
    uvicorn.run("api.server:app", host=host, port=port, log_level="info")

if __name__ == "__main__":
    typer.run(main)