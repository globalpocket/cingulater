from fastmcp import FastMCP
import instructor
from litellm import completion
from pydantic import BaseModel, Field, create_model
from typing import Any, Dict, List, Optional, Type
import logging

# Logger settings
logger = logging.getLogger(__name__)

mcp = FastMCP("llm_validator")

# --- Schemas from original schemas.py ---

class IntentDraft(BaseModel):
    """Phase 0: 意図のドラフト"""
    intent_summary: str = Field(..., description="ユーザーの意図を簡潔にまとめたもの")
    evaluation_axes: List[str] = Field(..., description="評価軸（評価の観点）のリスト")
    draft_comment: str = Field(..., description="ユーザーへ提示するドラフトコメント")
    required_mcp_servers: List[str] = Field(
        default_factory=list,
        description="必要なJITロードMCPサーバーのリスト"
    )

class AnalysisProposal(BaseModel):
    """Phase 1: 分析計画"""
    dependency_critical_nodes: List[str] = Field(..., description="解析すべき重要コンポーネント")
    questions_to_user: List[str] = Field(..., description="不確実性を排除するための質問リスト")

class RingiDocument(BaseModel):
    """Phase 4: 稟議書"""
    summary: str = Field(..., description="発生した事象の概要")
    impact_analysis: str = Field(..., description="影響範囲の分析")
    proposed_fix: str = Field(..., description="具体的な修正案")
    risk_assessment: str = Field(..., description="リスク評価")

SCHEMA_MAP = {
    "intent_draft": IntentDraft,
    "analysis_proposal": AnalysisProposal,
    "ringi_document": RingiDocument
}

@mcp.tool()
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
