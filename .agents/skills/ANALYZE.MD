---
name: project-analysis
description: Automatically performs a comprehensive project analysis (Repomix, Ruff, Semgrep, Bandit, ast-grep) when source code changes are detected in the src directory. This skill ensures architectural integrity and catches bugs early.
---

# Project Analysis Skill

This skill is responsible for maintaining project hygiene and security by running a suite of analysis tools.

## Trigger Details
- **Condition**: Any file modification within the `src/` directory.
- **Action**: Execute the dedicated analyzer script.

## Execution Requirements
1. **Singleton Execution**: Only one instance of the analyzer should run at a time.
2. **Debounce Logic**: Wait for a short period (last-one-wins) if multiple changes occur in rapid succession to avoid resource exhaustion.
3. **Command**: `python3 .agents/scripts/agent_analyzer.py`

## Output
- All reports are persisted in the `.analyze/` directory.
