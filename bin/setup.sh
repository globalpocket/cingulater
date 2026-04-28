#!/bin/bash
set -e

# Brownie Lightweight Setup Script

echo "1. Checking uv installation..."
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
else
    echo "uv is already installed."
fi

echo "2. Installing Python dependencies..."
uv sync

echo "3. Setting up environment variables..."
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    # 最小限の環境変数をセット
    cat << EOF > .env
BROWNIE_LANGUAGE=ja
BROWNIE_DEBUG=true
EOF
else
    echo ".env already exists."
fi

# 実行権限の付与
chmod +x bin/brwn

echo "================================================="
echo "✅ Setup complete! You can now run Brownie."
echo "Command: ./bin/brwn"
echo "================================================="