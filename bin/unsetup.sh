#!/bin/bash
set -e

# Cingulater 環境削除スクリプト (Unsetup)
# 0. Docker サービスの停止とリソース削除 (ChromaDB 等)
echo "Stopping any running Cingulater processes..."
./bin/cingulater stop &> /dev/null || true

if command -v docker-compose &> /dev/null || docker compose version &> /dev/null; then
    echo "Stopping Docker services and removing volumes..."
    if docker compose version &> /dev/null; then
        docker compose down -v || true
    else
        docker-compose down -v || true
    fi
fi

# 1. uv コマンドのパス解決
UV_CMD="$HOME/.local/bin/uv"
if command -v uv &> /dev/null; then
    UV_CMD="uv"
fi

# 1. 設定の読み込み (削除前に実施)
MODEL_DIR="~/.local/share/cingulater/models"
if [ -f "config.yaml" ]; then
    # uv が使える場合は優先使用、使えない場合は grep で簡易取得
    if command -v uv &> /dev/null && [ -d ".venv" ]; then
        MODEL_DIR=$(uv run python -c "import yaml; print(yaml.safe_load(open('config.yaml'))['llm'].get('model_dir', '~/.local/share/cingulater/models'))" 2>/dev/null || echo "~/.local/share/cingulater/models")
    else
        MODEL_DIR=$(grep 'model_dir:' config.yaml | awk '{print $2}' | tr -d '"' | tr -d "'" || echo "~/.local/share/cingulater/models")
    fi
fi
EXPANDED_MODEL_DIR=$(echo $MODEL_DIR | sed "s|^~|$HOME|")

# 1. Python 仮想環境の削除
if [ -d ".venv" ]; then
    echo "Removing Python virtual environment (.venv)..."
    rm -rf .venv
fi

# 2. ローカルデータの削除 (データベース, ベクトルDB 等)
echo "Resolving data paths from config.yaml for cleanup..."
# 仮想環境が削除された後でも実行できるよう --with pyyaml を指定
$UV_CMD run --with pyyaml python3 -c "
import yaml
import os
import shutil

if not os.path.exists('config.yaml'):
    print('config.yaml not found. Skipping data cleanup.')
    exit(0)

with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# 削除対象: DB本体, ベクトルDB/Memory, ワークスペース, 管理ファイル(PID/Lock)
to_delete = [
    config['database'].get('db_path'),
    config['database'].get('memory_path'),
    config['workspace'].get('base_dir'),
    os.path.join(os.path.dirname(config['database'].get('db_path', '')), 'cingulater.pid'),
    os.path.join(os.path.dirname(config['database'].get('db_path', '')), 'cingulater.lock')
]

for p in to_delete:
    if p and os.path.dirname(p): # パスが有効か確認
        expanded = os.path.expanduser(p)
        if os.path.exists(expanded):
            print(f'Removing: {expanded}')
            if os.path.isdir(expanded):
                shutil.rmtree(expanded)
            else:
                os.remove(expanded)
"

# 3. キャッシュの削除 (Tree-sitter 文法ファイル等)
CACHE_DIR="$HOME/.cache/cingulater"
if [ -d "$CACHE_DIR" ]; then
    echo "Removing cache directory ($CACHE_DIR)..."
    rm -rf "$CACHE_DIR"
fi

# 4. ログの削除
if [ -d "logs" ]; then
    echo "Removing logs directory..."
    rm -rf logs
fi

# プラグインのキャッシュ等のクリーンアップ
echo "Cleaning up MCP plugins cache..."
rm -rf src/mcp_server/plugins/__pycache__
rm -rf src/mcp_server/__pycache__

# 5. 環境設定ファイルの削除
if [ -f ".env" ]; then
    read -p "Do you want to remove the .env file (containing GitHub token)? [y/N]: " REMOVE_ENV
    if [[ "$REMOVE_ENV" =~ ^[Yy]$ ]]; then
        echo "Removing .env file..."
        rm .env
    fi
fi

# 6. シェルエイリアスの削除 (~/.zshrc)
if [ -f "$HOME/.zshrc" ]; then
    if grep -q "alias cingulater=" "$HOME/.zshrc"; then
        echo "Removing cingulater alias from ~/.zshrc..."
        # 該当行を削除した一時ファイルを作成し、上書き
        sed -i.bak '/alias cingulater=/d' "$HOME/.zshrc"
        rm "${HOME}/.zshrc.bak"
    fi
fi

# 7. Persistent Model Storage (永続化モデル) の削除
read -p "Do you want to remove all AI models stored in $MODEL_DIR? [y/N]: " REMOVE_MODELS
if [[ "$REMOVE_MODELS" =~ ^[Yy]$ ]]; then
    echo "Removing persistent model directory..."
    rm -rf "$EXPANDED_MODEL_DIR"
    
    # 以前のキャッシュディレクトリが残っている場合も念のため削除
    echo "Cleaning up legacy cache directories if exist..."
    rm -rf "$HOME/.cache/huggingface/hub/models--mlx-community*"
    rm -rf "$HOME/.cache/huggingface/hub/models--google--gemma*"
fi

# 8. システムツール (brew/aptで入れたもの) について
echo ""
echo "Note: System-wide tools (Node.js, Docker, Ollama, etc.) were not removed."
echo "If you want to uninstall them, please use your package manager (brew/apt) manually."
echo ""
echo "✅ Cingulater environment has been uninstalled successfully."