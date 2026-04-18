import asyncio
import os
import sys

# プロジェクトルートをパスに追加
sys.path.append(os.getcwd())

async def verify_logic():
    print("Checking StateManager and connect() method...")
    try:
        from src.core.state_manager import StateManager
        sm = StateManager()
        
        # 1. connect() メソッドの存在と実行を確認
        if not hasattr(sm, 'connect'):
            print("FAILED: StateManager has no 'connect' method.")
            return
        
        await sm.connect()
        print("SUCCESS: StateManager.connect() executed successfully.")
        
        # 2. get_state_lightweight の動作確認
        from src.core.workers.pool import REDIS_HOST
        print(f"Checking Redis connection at {REDIS_HOST}...")
        res = await sm.get_state_lightweight("globalpocket/brownie#45")
        print(f"SUCCESS: get_state_lightweight returned: {res}")
        
        # 3. tasks からのインポート確認
        print("Checking analysis_task import...")
        from src.core.workers.tasks import analysis_task
        print("SUCCESS: analysis_task importable.")
        
        print("\n--- ALL TESTS PASSED ---")
        
    except Exception as e:
        print(f"FAILED with error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(verify_logic())
