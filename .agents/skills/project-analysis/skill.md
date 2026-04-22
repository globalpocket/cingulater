---
name: project-analysis
description: IMMEDIATELY ACTIVATE this skill to perform a comprehensive project analysis whenever any file modification or addition is detected within the 'src/' directory. The agent must proactively execute 'python3 .agents/scripts/agent_analyzer.py' to ensure code quality and architectural integrity.
---

# Project Analysis Skill

This skill allows the agent to autonomously maintain project hygiene by running critical analysis tools whenever the source code changes.

## Autonomous Trigger Policy
- **Detection**: The agent monitors all file activities in the `src/` directory.
- **Activation**: Upon any modification in `src/`, the agent must decide to run the analysis suite immediately.
- **Singleton Control**: The agent ensures only one analysis instance runs at a time, suppressing redundant triggers if multiple files change rapidly.

## Step-by-Step Guidance
1. **Change Detection**: Acknowledge that files in `src/` have been updated.
2. **Process Check**: Verify that `python3 .agents/scripts/agent_analyzer.py` is not already running.
3. **Execution**: Run the command: `python3 .agents/scripts/agent_analyzer.py`.
4. **Finalization**: Confirm that reports are generated in the `.analyze/` directory.
