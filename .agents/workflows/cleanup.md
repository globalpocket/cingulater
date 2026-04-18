# Workflow: Agent Cleanup (/cleanup)

このワークフローは、AI エージェントが作業を終える際、または環境にプロセスが残留している疑いがある場合に実行する標準手順です。

## 🎯 Goal
エージェントが使用したブラウザ（Chromium, headless_shell）および関連プロセスを完全に一掃し、環境を清浄な状態（Hygiene OK）に戻す。

## 🛠 Steps

### 1. Identify Orphans
まず現状を確認します。
- **Command**: `python3 .agent/scripts/hygiene_check.py`

### 2. Kill Zombies
もしプロセスが残っている場合は、ピンポイントで強制終了させます。
- **Command**: `pkill -9 -f "headless_shell"`
- **Command**: `pkill -9 -f "Chromium"`
- **Command**: `pkill -9 -f "playwright"`

### 3. Verify
再度チェックを行い、正常終了（Exit 0）することを確認します。
- **Command**: `python3 .agent/scripts/hygiene_check.py`

## 📝 Best Practice
作業を「完了」としてユーザーに報告する直前に、必ずこの手順（またはスクリプトの実行）を含めること。
