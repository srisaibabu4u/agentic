# Working with the KYC orchestrator in chat

This repo is a prototype of a master agent that routes a client risk assessment
renewal through five specialised agents and a maker / checker review. All data is
dummy sample data.

The `kyc-orchestrator` MCP server exposes the workflow as tools. Use them instead of
running scripts when the user asks to start, inspect or act on a case.

## Roles
- You are the operator. The master and agents do the work; the user plays the
  maker (Priya Nair) or the checker (Daniel Tan).
- Only submit a maker or checker action when the user asks for it or gives you the
  content. Never invent a screening disposition, field confirmation or approval.
- Maker and checker actions are only accepted when the case is waiting on that role.
  Check `waiting_on` in get_case_status first.

## Typical flow
1. `start_case` (mode "interactive") → runs until a human is needed.
2. `get_case_status` / `get_answers` / `explain_decisions` to show the user what happened and why.
3. `maker_submit` with the user's uploads, field_edits and dispositions.
   Sample uploads are in `samples/` (pass `file_path`).
4. `checker_return` or `checker_approve` on the user's instruction.

When explaining, quote the master's rules and guardrails (R1–R9, G1–G6) and rationale
from `explain_decisions`, not your own guess. With a live master, also say who decided
(`decided_by`) and, for `JUDGEMENT`, what the playbook would have done (`playbook`).

## Code
- orchestrator/master.py  routing rules + guardrails (the playbook; the offline master)
- orchestrator/master_agent.py  live master: Claude decides, guardrails enforced in code
- orchestrator/agents.py  the five agents (offline logic + live task briefs)
- orchestrator/toolkit.py live agents' read-only tools and result checks
- orchestrator/llm.py     the agentic tool-use loop (run_agent)
- orchestrator/engine.py  event loop, only code that writes the case
- prompts/                agent system prompts
- tests/test_routing.py   run after any change to master.py
- tests/test_agentic.py   run after any change to agents.py, toolkit.py or llm.py
- tests/test_master_agent.py  run after any change to master.py or master_agent.py
