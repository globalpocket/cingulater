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
    
    planner_model_name = config['llm']['models'].get('planner', 'mlx-community/gemma-4-26b-a4b-it-4bit')
    planner_endpoint = config['llm']['planner_endpoint']
    
    # 堅牢なモデルの取得
    from src.llm.robust_model import get_robust_model
    model = get_robust_model(planner_model_name, base_url=planner_endpoint)

    agent = Agent(
        model,
        output_type=IntentDraft,
        system_prompt=(
            "あなたは Brownie (ブラウニー) という、ユーザーとともにソフトウェアを育てる優秀なエンジニアです。\n"
            "あなたの役割は、ユーザーの要望をエンジニアリング要件に翻訳し、スムーズな実行計画を立てる事です。\n\n"
            "### ミッション\n"
            "1. 現在の `instruction` (指示) に含まれる最新の情報を読み取り、過去の自分との対話やユーザーの補足事項を全て把握してください。\n"
            "2. ユーザーがすでに提供した情報（例：技術スタックの指定、既存コンテンツの有無など）を再度質問しないでください。それは『会話になっていない』と判断される最大の原因です。\n"
            "3. ユーザーの最新のコメントが情報を補足するものであれば、感謝を述べ、その情報がどのように計画に反映されたかを説明してください。\n\n"
            "### 会話の指針\n"
            "- 挨拶: 親切でプロフェッショナルな挨拶。\n"
            "- 文脈認識: 『〜についての情報をいただきありがとうございます』など、提供された情報を反映していることを示す。\n"
            "- 進行の提案: 情報が十分なら『次はリポジトリの解析を行い、具体的な構造を調査します。よろしいでしょうか？』と次のフェーズ（Phase 1）への移行を促す。\n\n"
            "### 出力制限\n"
            "- `draft_comment` に全てのユーザー向けメッセージを記述してください。\n"
            "- 無機質なテンプレートだけを返すのではなく、必ず血の通ったエンジニアとしての返答を生成してください。"
        )
    )
    
    try:
        result = await agent.run(state['instruction'])
        draft: IntentDraft = result.output
        
        # ユーザーへの最終メッセージを構築
        formatted_draft = draft.draft_comment
        
        # もし要約や評価軸が空でなければ、詳細として付加する
        if draft.intent_summary:
            formatted_draft += (
                f"\n\n---\n"
                f"**🎯 整理された目標:** {draft.intent_summary}\n"
                f"**✅ 成功基準:** " + ", ".join(draft.evaluation_axes) + "\n"
                f"**🛠 使用ツール候補:** " + ", ".join(draft.required_mcp_servers)
            )

        return {
            "status": "Phase0_WaitingForUserConfirmation",
            "intent_confirmed": False,
            "intent_draft": formatted_draft,
            "evaluation_axes": draft.evaluation_axes,
            "required_mcp_servers": draft.required_mcp_servers,
            "history": [{"node": "intent_alignment", "status": "draft_updated"}]
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
