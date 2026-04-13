import subprocess
import os

VERSION = "0.1.0--alpha"

def get_build_id():
    """現在のGitコミットハッシュを取得し、ビルドIDとして返す"""
    try:
        # スクリプトの場所を基準にリポジトリルートを特定
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        build_id = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], 
            cwd=repo_root,
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        return build_id
    except Exception:
        return VERSION

def get_footer():
    """GitHubコメント用の標準フッターを生成する"""
    return f"\n\n---\n> Built from: `{get_build_id()}`"
