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
    print(f"--- Phase 0: Intent Alignment ({state.get('task_id', 'unknown')}) ---")
    
    with open('config/config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    planner_model_name = config['llm']['models'].get('planner', 'gemma-4-26b-it-4bit')
    planner_endpoint = config['llm']['planner_endpoint']
    
    import os
    os.environ.setdefault("OPENAI_API_KEY", "EMPTY")

    # Pydantic AI 1.x では環境変数から自動的に設定されるため、明示的に os.environ に反映
    if planner_endpoint:
        os.environ["OPENAI_BASE_URL"] = planner_endpoint
    
    # 完全に引数なしの標準的な初期化
    model = OpenAIModel(planner_model_name)

    agent = Agent(
        model,
        output_type=IntentDraft,
        system_prompt=(
            "あなたは Brownie (ブラウニー) という、ユーザーをサポートする親切で優秀なエンジニアエージェントです。\n"
            "ユーザーの要求を深く理解し、単に作業を始めるのではなく、一歩引いて『期待されていること』を言語化してください。\n\n"
            "【会話のスタイル】\n"
            "- 自然な日本語で、エンジニアとして対等かつサポートに徹する姿勢で話してください。\n"
            "- ユーザーの『対話的に進めたい』という要望を尊重し、まず今回の目標と計画を提示して承認を求めてください。\n\n"
            "【出力項目のガイドライン】\n"
            "- 意図の要約: ユーザーの言葉をエンジニアリング的な視点で再定義したもの。 (e.g., GitHub Pages の設定と CI/CD の構築、等)\n"
            "- 成果の評価軸: どのような状態になれば『成功』と言えるかの基準を 3つ程度。\n"
            "- 確認コメント: 実装に入る前にユーザーに確認したい点や、挨拶を含む返信。"
        )
    )
    
    try:
        result = await agent.run(state['instruction'])
        draft: IntentDraft = result.output
        
        formatted_draft = (
            f"{draft.draft_comment}\n\n"
            f"--- 📋 **Brownieの理解と提案** ---\n"
            f"**🎯 目標:** {draft.intent_summary}\n\n"
            f"**✅ 成功基準 (Evaluation Axes):**\n" + "\n".join([f"- {a}" for a in draft.evaluation_axes]) + "\n\n"
            f"**🛠 使用予定の特殊ツール:**\n{draft.required_mcp_servers}\n\n"
            f"内容に問題がなければ、承認の旨をお伝えください。確認後、詳細なリポジトリ分析を開始します！"
        )
        
        return {
            "status": "Phase0_Alignment",
            "intent_confirmed": False,
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
