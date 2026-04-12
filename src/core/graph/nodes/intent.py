import yaml
from typing import Dict, Any
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from openai import AsyncOpenAI
from src.core.graph.state import TaskState
from src.core.validation.schemas import IntentDraft

async def intent_alignment_node(state: TaskState) -> Dict[str, Any]:
    """
    Phase 0: Intent Alignment
    ユーザーの意図を汲み取り、評価軸（Evaluation Axes）とJITツール選定を提示して合意を得る。
    """
    print(f"--- Phase 0: Intent Alignment ({state['task_id']}) ---")
    
    with open('config/config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    planner_model_name = config['llm']['models'].get('planner', 'gemma-4-26b-it-4bit')
    planner_endpoint = config['llm']['planner_endpoint']
    
    import os
    os.environ.setdefault("OPENAI_API_KEY", "EMPTY")

    # OpenAI クライアントを明示的に作成して渡す（最も確実な方法）
    client = AsyncOpenAI(
        base_url='http://localhost:11434/v1',
        api_key=os.getenv("OPENAI_API_KEY", "EMPTY")
    )
    model = OpenAIModel(planner_model_name, openai_client=client)

    agent = Agent(
        model,
        output_type=IntentDraft,
        system_prompt=(
            "あなたはシニアソフトウェアエンジニアです。"
            "ユーザーの要求を分析し、意図の要約、成果物を評価するための軸、ユーザーへの確認コメント、"
            "およびタスク解決に必要なJITロードMCPサーバーを選択してください。\n\n"
            "【サーバー選択ガイドライン】\n"
            "- 基本的なファイル操作(workspace_server)等は常に利用可能なので選択不要です。分析や可視化などに必要な特別ツールのみを選択してください。\n"
            "- 最大で3〜5個に絞ってください。\n"
            "- 候補: web_fetch, graph_memory, meta_search, design_pattern_oracle, arch_diagram, api_analyzer, security_analyzer, clone_detector, test_coverage, git_archeology, db_profiler, dep_audit, trace_analyzer"
        )
    )
    
    try:
        result = await agent.run(state['instruction'])
        draft: IntentDraft = result.data
        
        formatted_draft = f"{draft.draft_comment}\n\n■ 提案される評価軸:\n- " + "\n- ".join(draft.evaluation_axes) + f"\n\n■ 要求される拡張ツール:\n{draft.required_mcp_servers}"
        
        return {
            "status": "Phase0_Alignment",
            "intent_confirmed": False, # 初回は False でユーザー確認待ちを促す想定
            "intent_draft": formatted_draft,
            "evaluation_axes": draft.evaluation_axes,
            "required_mcp_servers": draft.required_mcp_servers,
            "history": [{"node": "intent_alignment", "status": "draft_created"}]
        }
    except Exception as e:
        print(f"Failed to generate intent draft: {e}")
        return {
            "status": "Phase0_Alignment",
            "intent_confirmed": False,
            "intent_draft": f"以下の意図で受け承りました: {state['instruction']}\n(自動生成に失敗しました: {e})",
            "evaluation_axes": ["要件適合性", "破壊的変更の有無"],
            "required_mcp_servers": [],
            "history": [{"node": "intent_alignment", "status": "draft_failed"}]
        }
