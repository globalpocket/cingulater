#!/bin/bash
set -e

# Brownie 統合セットアップスクリプト (設計書 11.1 - スマート版)
echo "Starting Brownie Provisioning..."

# macOS (Apple Silicon) のパスを考慮
if [[ "$(uname)" == "Darwin" ]]; then
    if [[ -f "/opt/homebrew/bin/brew" ]] && ! command -v brew &> /dev/null; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi

# ツール存在チェック関数
# $1: コマンド名, $2: Macアプリ名 (省略可)
check_tool() {
    local cmd=$1
    local app=$2
    
    # コマンドがすでに存在するかチェック
    if command -v "$cmd" &> /dev/null; then
        echo "Found existing command: $cmd ($(which $cmd))"
        return 0
    fi
    
    # Mac特有のアプリケーションパスをチェック
    if [[ "$(uname)" == "Darwin" ]] && [[ -n "$app" ]] && [[ -d "/Applications/$app" ]]; then
        echo "Found existing Application: /Applications/$app"
        return 0
    fi
    
    return 1
}

# 1. OS チェック
OS="$(uname)"
case $OS in
  "Darwin")
    echo "Running on macOS..."
    # Homebrew
    if ! command -v brew &> /dev/null; then
        echo "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi

    echo "Checking for missing tools..."
    TOOLS_TO_INSTALL=()
    
    # git-lfs
    if ! check_tool "git-lfs"; then TOOLS_TO_INSTALL+=("git-lfs"); fi
    # Docker (Application or CLI)
    if ! check_tool "docker" "Docker.app"; then TOOLS_TO_INSTALL+=("docker" "docker-compose"); fi
    # Node.js (for Repomix & Prettier)
    if ! check_tool "node"; then TOOLS_TO_INSTALL+=("node"); fi
    # ast-grep (Semantic search/replace)
    if ! check_tool "sg"; then TOOLS_TO_INSTALL+=("ast-grep"); fi
    # C Compiler (for Tree-sitter build)
    if ! xcode-select -p &> /dev/null; then
        echo "Xcode Command Line Tools not found. Installing..."
        xcode-select --install || true # すでに対話型のインストーラが走っている場合はエラーになるので続行
    fi
    
    if [ ${#TOOLS_TO_INSTALL[@]} -gt 0 ]; then
        echo "Installing missing tools: ${TOOLS_TO_INSTALL[*]}"
        brew install "${TOOLS_TO_INSTALL[@]}"
    else
        echo "All system tools are already installed. Skipping brew install."
    fi
    ;;
    
  "Linux")
    echo "Running on Linux..."
    sudo apt update
    # Linux では基本的にパッケージマネージャ経由で一括管理
    # build-essential: Cコンパイラ, nodejs/npm: Repomix実行用
    sudo apt install -y git-lfs docker.io docker-compose-v2 curl build-essential nodejs npm
    
    # ast-grep (Semantic search/replace) - Linux 用
    if ! check_tool "sg"; then
        echo "Installing ast-grep via npm..."
        sudo npm install -g @ast-grep/cli || true
    fi
    
    sudo apt install -y git-lfs docker.io docker-compose-v2 curl build-essential nodejs npm
    ;;
  *)
    echo "Unsupported OS: $OS"
    exit 1
    ;;
esac

# 2. Git LFS インストール
echo "Initializing Git LFS..."
git lfs install

# 3. Python 仮想環境 (uv) の構築
UV_CMD="$HOME/.local/bin/uv"
if ! command -v uv &> /dev/null && [ ! -f "$UV_CMD" ]; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# PATHの反映とコマンドの確定
export PATH="$HOME/.local/bin:$PATH"
if [ -f "$HOME/.local/bin/env" ]; then
    source "$HOME/.local/bin/env"
fi
# インストール直後などでパスが通っていない場合への対応
if ! command -v uv &> /dev/null; then
    UV_CMD="$HOME/.local/bin/uv"
else
    UV_CMD="uv"
fi

echo "Syncing Python dependencies (including Pydantic and MLX)..."
# すべての依存関係（mlx-lm, outlines 等を含む）を pyproject.toml に集約したため、sync だけで完了する
$UV_CMD sync

# 4. ディレクトリ初期化
echo "Initializing directories from config.yaml..."
# 設定ファイルからパスの設定を動的に取得して初期化
$UV_CMD run python -c "
import yaml
import os

with open('config/config.yaml', 'r') as f:
    config = yaml.safe_load(f)

paths = [
    config['database'].get('db_path'),
    config['database'].get('memory_path'),
    config['llm'].get('model_dir'),
    config['workspace'].get('base_dir')
]

for p in paths:
    if p:
        expanded = os.path.expanduser(p)
        dir_path = os.path.dirname(expanded) if '.' in os.path.basename(expanded) else expanded
        print(f'Ensuring directory: {dir_path}')
        os.makedirs(dir_path, exist_ok=True)
"

mkdir -p logs
mkdir -p src/mcp_server/plugins/

echo "Pre-downloading Semgrep rules..."
sg --help >/dev/null 2>&1 || true # ignore if not fully valid
bandit --help >/dev/null 2>&1 || true

# 5. 環境設定 (.env)
if [ ! -f ".env" ]; then
    touch .env
fi

if ! grep -q "GITHUB_TOKEN=" .env; then
    echo "Configuring GitHub Access Token..."
    read -p "Enter your GitHub Personal Access Token (classic, repo scope): " TOKEN
    if [[ -n "$TOKEN" ]]; then
        echo "GITHUB_TOKEN=$TOKEN" >> .env
        echo "GITHUB_TOKEN added to .env."
    else
        echo "Warning: GITHUB_TOKEN was not provided. You will need to set it manually in .env."
    fi
fi

if ! grep -q "BROWNIE_LANGUAGE=" .env; then
    echo "Select your preferred communication language:"
    echo "1) Japanese (日本語)"
    echo "2) English"
    echo "3) Chinese (简体中文)"
    echo "4) Chinese (繁體中文)"
    echo "5) Korean (한국어)"
    echo "6) French (Français)"
    echo "7) German (Deutsch)"
    echo "8) Spanish (Español)"
    read -p "Enter Choice [1-8, default: 1]: " LANG_CHOICE
    case $LANG_CHOICE in
        1) LANG="Japanese";;
        2) LANG="English";;
        3) LANG="Chinese (Simplified)";;
        4) LANG="Chinese (Traditional)";;
        5) LANG="Korean";;
        6) LANG="French";;
        7) LANG="German";;
        8) LANG="Spanish";;
        *) LANG="Japanese";;
    esac
    echo "BROWNIE_LANGUAGE=$LANG" >> .env
    echo "Communication language set to: $LANG"
fi

# 6. LLM モデルの事前ダウンロード (MLX 用)
echo "Downloading models dynamically from config.yaml..."
# モデルの保存先を config/config.yaml から取得 (デフォルト: ~/.local/share/brownie/models)
MODEL_DIR=$($UV_CMD run python -c "import yaml; print(yaml.safe_load(open('config/config.yaml'))['llm'].get('model_dir', '~/.local/share/brownie/models'))")
export HF_HOME=$(echo $MODEL_DIR | sed "s|^~|$HOME|")
mkdir -p "$HF_HOME"

$UV_CMD run python -c "
import yaml
from huggingface_hub import snapshot_download

with open('config/config.yaml', 'r') as f:
    config = yaml.safe_load(f)

models = config.get('llm', {}).get('models', {})
for role, model_name in models.items():
    print(f'Downloading {role} model: {model_name} (This may take a while)...')
    snapshot_download(model_name)
"

# 6. 保守・保護設定
if ! grep -q "alias brownie=" ~/.zshrc 2>/dev/null; then
    echo "Adding alias to ~/.zshrc..."
    echo "alias brownie='nice -n 10 ./bin/brwn'" >> ~/.zshrc
fi

# 6. Docker ボリュームの初期化
if command -v docker-compose &> /dev/null || docker compose version &> /dev/null; then
    echo "Initializing Docker services..."
    # 'docker compose' (V2) を優先使用
    if docker compose version &> /dev/null; then
        docker compose up -d chromadb redis
    else
        docker-compose up -d chromadb redis
    fi
else
    echo "Warning: Docker not found. Skipping service initialization."
fi

# 7. LLM 推奨モデルの準備 (MLX)
# 上記のセクション 4 で Qwen 3.5 モデルの事前ダウンロードが実行されています。

# 8. 高度な解析エンジンのセットアップ (Tree-sitter Grammars)
echo "Setting up advanced analysis engine (Tree-sitter)..."

echo "Brownie setup completed successfully!"
