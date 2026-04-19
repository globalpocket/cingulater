# Implementation Plan - Wave 2: セキュリティ要塞化

## Proposed Changes

### [Core Utils]
- **cmd_helper.py**: `shell=True` を排除し、`shlex.split` を用いた安全なコマンド実行へ移行。

### [Core Logic]
- **trigger_manager.py**: `eval()` を廃止、安全な式評価器へ置換。
- **sandbox_manager.py**: リンター実行時のコマンド構築をリスト形式へ変更。

## 職人の作業手順
1. `cmd_helper.py` の修正。
2. `sandbox_manager.py` の修正。
3. `trigger_manager.py` の修正。
