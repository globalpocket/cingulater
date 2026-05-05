#!/bin/bash
set -e

# Cingulater 統合セットアップスクリプト
echo "Starting Cingulater Provisioning..."

# macOS (Apple Silicon) のパスを考慮
if [[ "$(uname)" == "Darwin" ]]; then
    if [[ -f "/opt/homebrew/bin/brew" ]] && ! command -v brew &> /dev/null; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi

# ツール存在チェック関数
check_tool() {
    local cmd=$1
    local app=$2
    if command -v "$cmd" &> /dev/null; then return 0; fi
    if [[ "$(uname)" == "Darwin" ]] && [[ -n "$app" ]] && [[ -d "/Applications/$app" ]]; then return 0; fi
    return 1
}

OS="$(uname)"
case $OS in
  "Darwin")
    echo "Running on macOS..."
    if ! command -v brew &> /dev/null; then
        echo "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    TOOLS_TO_INSTALL=()
    if ! check_tool "git-lfs"; then TOOLS_TO_INSTALL+=("git-lfs"); fi
    if ! check_tool "docker" "Docker.app"; then TOOLS_TO_INSTALL+=("docker" "docker-compose"); fi
    if ! check_tool "node"; then TOOLS_TO_INSTALL+=("node"); fi
    if ! check_tool "sg"; then TOOLS_TO_INSTALL+=("ast-grep"); fi
    if ! xcode-select -p &> /dev/null; then xcode-select --install || true; fi
    if [ ${#TOOLS_TO_INSTALL[@]} -gt 0 ]; then brew install "${TOOLS_TO_INSTALL[@]}"; fi
    ;;
  "Linux")
    echo "Running on Linux..."
    sudo apt update
    sudo apt install -y git-lfs docker.io docker-compose-v2 curl build-essential nodejs npm
    if ! check_tool "sg"; then sudo npm install -g @ast-grep/cli || true; fi
    ;;
esac

echo "Initializing Git LFS..."
git lfs install

UV_CMD="$HOME/.local/bin/uv"
if ! command -v uv &> /dev/null && [ ! -f "$UV_CMD" ]; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv &> /dev/null; then UV_CMD="$HOME/.local/bin/uv"; else UV_CMD="uv"; fi

echo "Syncing Python dependencies..."
$UV_CMD sync
# 動的ダウンロードとLLM起動に必要なパッケージを明示的に追加
$UV_CMD pip install pyyaml huggingface_hub mlx-lm

mkdir -p logs

# 🎯 動的モデルダウンロード (ハードコード排除)
echo "Downloading models dynamically from config.yaml..."
$UV_CMD run python -c "
import yaml
import os
from huggingface_hub import snapshot_download

config_path = 'config.yaml' if os.path.exists('config.yaml') else 'config/config.yaml'
if not os.path.exists(config_path):
    print(f'Warning: {config_path} not found. Skipping model download.')
else:
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        models = config.get('llm', {}).get('models', {})
        for role, model_name in models.items():
            print(f'Downloading {role} model: {model_name} (This may take a while if not cached)...')
            snapshot_download(model_name)
    except Exception as e:
        print(f'Error reading config or downloading: {e}')
"

if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cat << EOF > .env
CINGULATER_LANGUAGE=ja
CINGULATER_DEBUG=true
EOF
fi

chmod +x bin/cingulater

if command -v docker-compose &> /dev/null || docker compose version &> /dev/null; then
    echo "Initializing Docker services..."
    if docker compose version &> /dev/null; then
        docker compose up -d chromadb redis || true
    else
        docker-compose up -d chromadb redis || true
    fi
fi

echo "================================================="
echo "✅ Setup complete! You can now run Cingulater."
echo "Command: ./bin/cingulater start"
echo "================================================="