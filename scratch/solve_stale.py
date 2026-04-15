import asyncio
import os
import sys
from src.gh_platform.client import GitHubClientWrapper
from src.core.state_manager import StateManager

async def solve_stale_task():
    sys.path.append(os.getcwd())
    
    # .env からトークンをロード
    from dotenv import load_dotenv
    load_dotenv()
    
    # 1. 自分をアサイン
    token = os.getenv('GITHUB_TOKEN', '')
    gh = GitHubClientWrapper(token)
    me = gh.get_my_username()
    repo_name = 'globalpocket/brownie-sampleproject'
    issue_num = 1
    
    print(f"Assigning {me} to {repo_name}#{issue_num}...")
    try:
        repo = gh.g.get_repo(repo_name)
        issue = repo.get_issue(issue_num)
        issue.add_to_assignees(me)
        print("Assignment successful.")
    except Exception as e:
        print(f"Assignment failed: {e}")

    # 2. DBのリセット
    state = StateManager('config/config.yaml') # 実際には config.yaml からパスを読むが、ここでは直接指定も可
    # config読込
    import yaml
    with open('config/config.yaml', 'r') as f:
        cfg = yaml.safe_load(f)
    state = StateManager(cfg['database']['db_path'])
    
    await state.connect()
    print("Clearing failed tasks from DB...")
    if state.conn:
        await state.conn.execute("DELETE FROM tasks WHERE repo_full_name = ? AND status = 'Failed'", (repo_name,))
        await state.conn.commit()
    await state.close()
    print("DB Reset successful.")

if __name__ == "__main__":
    asyncio.run(solve_stale_task())
