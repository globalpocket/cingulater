import inspect
import os
import sys
import traceback
from pathlib import Path

# プロジェクトルートをパスに追加
base_dir = Path(__file__).parent.parent.parent
sys.path.append(str(base_dir))


def trace_initialization():
    print(f"--- Brownie Diagnostic Tool (Build: {os.getpid()}) ---")
    print(f"CWD: {os.getcwd()}")
    print(f"Python Executable: {sys.executable}")

    try:
        # 1. builder モジュールの調査
        print("\n[1] Investigating src.core.graph.builder...")
        import src.core.graph.builder as builder

        builder_file = inspect.getsourcefile(builder)
        print(f"  - Source file: {builder_file}")

        # create_brownie_graph の調査
        func = builder.create_brownie_graph
        is_async = inspect.iscoroutinefunction(func)
        print(f"  - create_brownie_graph: {func}")
        print(f"  - Is Async (coroutinefunction): {is_async}")

        if is_async:
            print("  - WARNING: create_brownie_graph is still ASYNC at runtime!")
        else:
            print("  - OK: create_brownie_graph is SYNCHRONOUS.")

        # compile_workflow の調査
        comp_func = builder.compile_workflow
        print(f"  - compile_workflow: {comp_func}")
        print(f"  - Is Async: {inspect.iscoroutinefunction(comp_func)}")

        # 2. 実行テスト
        print("\n[2] Attempting compilation test...")
        try:
            res = comp_func()
            print(f"  - Result: {res}")
        except Exception as e:
            print("  - FAILED during compilation!")
            print(f"  - Error type: {type(e)}")
            print(f"  - Error message: {e}")
            traceback.print_exc()

        # 3. workflow_manager の調査 (別の .compile() 呼び出し箇所)
        print("\n[3] Investigating src.core.workflow_manager...")
        try:
            from langgraph.graph import StateGraph

            import src.core.workflow_manager as wm

            builder_inst = StateGraph(wm.DynamicWorkflowState)
            print(f"  - StateGraph.compile: {builder_inst.compile}")
        except Exception as e:
            print(f"  - Error investigating workflow_manager: {e}")

    except ImportError as e:
        print(f"\n[!] Critical Import Error: {e}")
        print("Maybe PYTHONPATH is not set correctly?")
    except Exception as e:
        print(f"\n[!] Unexpected Error: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    trace_initialization()
