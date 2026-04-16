from ..base_server import create_mcp_server, mcp_tool_errorhandler, setup_logging
import instructor
from litellm import completion
from pydantic import BaseModel, Field, create_model
from typing import Any, Dict, List, Optional, Type

# Logger settings
logger = setup_logging(__name__)
mcp = create_mcp_server("llm_validator")

from src.core.types import IntentDraft, AnalysisProposal, RingiDocument

SCHEMA_MAP = {
    "intent_draft": IntentDraft,
    "analysis_proposal": AnalysisProposal,
    "ringi_document": RingiDocument
}

@mcp.tool()
@mcp_tool_errorhandler
async def validate_and_extract(prompt: str, schema_type: str, model_name: str = "gemini/gemini-pro") -> str:
    """LLMからの出力を特定のスキーマに従って検証・抽出します。
    
    Args:
        prompt: LLMへの命令
        schema_type: 使用するスキーマ名 ('intent_draft', 'analysis_proposal', 'ringi_document')
        model_name: 使用するモデル名 (LiteLLM形式)
    """
    if schema_type not in SCHEMA_MAP:
        return f"Error: Unknown schema_type '{schema_type}'. Available: {list(SCHEMA_MAP.keys())}"

    response_model = SCHEMA_MAP[schema_type]
    
    try:
        # Instructor with LiteLLM
        client = instructor.from_litellm(completion)
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are a precise data extractor. Output only valid JSON matching the schema."},
                {"role": "user", "content": prompt}
            ],
            response_model=response_model,
            max_retries=3
        )
        return response.model_dump_json(indent=2)
        
    except Exception as e:
        logger.error(f"Validation failed: {e}")
        return f"Validation Error: {e}"

@mcp.tool()
@mcp_tool_errorhandler
async def validate_dynamic_schema(prompt: str, schema_json: str, model_name: str = "gemini/gemini-pro") -> str:
    """JSON Schema文字列を動的にPydanticモデルに変換し、LLM出力を検証します。
    
    Args:
        prompt: LLMへの命令
        schema_json: JSON Schema形式の文字列
        model_name: 使用するモデル名
    """
    import json
    try:
        schema_dict = json.loads(schema_json)
        # 簡易的な動的モデル生成 (旧bridge.pyのロジックを簡略化して統合)
        fields = {}
        for field_name, prop in schema_dict.get("properties", {}).items():
            fields[field_name] = (Any, Field(None, description=prop.get("description", "")))
        
        dynamic_model = create_model("DynamicModel", **fields)
        
        client = instructor.from_litellm(completion)
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            response_model=dynamic_model
        )
        return response.model_dump_json(indent=2)
    except Exception as e:
        return f"Dynamic validation failed: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
