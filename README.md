# KYC renewal: master + specialised agents

> **Status:** moving from the prototype below to a production-style app (React + Vite UI, FastAPI,
> PostgreSQL 16, JWT maker/checker/admin roles, agents offline by default with optional Claude).
> Phase 1 (database, migrations, dummy-data seed) is in [backend/](backend/). See
> [Production backend](#production-backend) at the end. The prototype still runs unchanged.

# Prototype

A runnable prototype of an autonomous client risk assessment renewal. A **master orchestrator** decides every next step and dispatches work to five **specialised agents**. The case then moves through **maker → automated review → checker** until it is approved and closed.

All data is dummy sample data: Halcyon Marine Logistics Pte Ltd, case RA-2026-0412.

## Run it

```bash
python run.py              # offline, deterministic, no API key needed
python run.py -v           # also prints notification bodies and raw agent JSON
python tests/test_routing.py   # 9 routing tests (or: python -m pytest -q)
```

The prototype needs Python 3.10+ and no third-party packages in offline mode.

To use Claude for some or all agents:

```bash
pip install anthropic
export ANTHROPIC_API_KEY=...
python run.py --live A3,A4          # drafting and review use Claude; the others stay offline
python run.py --live all --shadow   # every agent live, and an LLM master shadows the rules
```

- `--model` or the `KYC_MODEL` environment variable picks the model. The default is `claude-sonnet-5-5`.
- In live mode each agent sends its system prompt from `prompts/` plus a JSON task payload, then validates the JSON it gets back.
- The live path has been checked for payload building and prompt loading only. It has not been run against the API from this environment.

## Use it from VS Code chat (agent mode)

The workflow is exposed as an **MCP server** (`mcp_server.py`). VS Code's agent-mode chat (GitHub Copilot) or Claude Code can then run cases, explain the master's decisions, and act as maker or checker.

**1. Set up once**

```bash
cd kyc_agents
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

On Windows, change `command` in `.vscode/mcp.json` to `${workspaceFolder}\\.venv\\Scripts\\python.exe`, and in `.mcp.json` to `.venv\\Scripts\\python.exe`.

**2a. GitHub Copilot agent mode**
1. Open the `kyc_agents` folder in VS Code.
2. Open `.vscode/mcp.json` and click **Start** above `kycOrchestrator`. It asks for an API key: press Enter to skip, which keeps the agents offline.
3. Open Chat, switch to **Agent** mode, and check the tools icon shows `kycOrchestrator` with 12 tools.
4. Copilot reads `.github/copilot-instructions.md` automatically.

**2b. Claude Code** (VS Code extension or terminal)
1. Open the folder.
2. Approve the `kyc-orchestrator` server from `.mcp.json` when prompted.
3. Claude Code reads `CLAUDE.md` automatically.

**3. Try these prompts**
- "Start the sample case and tell me why it stopped."
- "Explain the master's last 3 decisions."
- "As maker: upload samples/DOC-08_audited_fs_fy2025.txt as the FY2025 audited FS (FYE 2025-12-31), confirm Seacrest at 45% against the signed register, and dismiss the Arun Menon hit (DOB 1971-04-02 vs 1958-09-17, nationality IN vs MY)."
- "As checker, return Q7: identify Seacrest's UBOs and their source of wealth."
- "As maker, upload the Seacrest register and SoW declaration from samples/."
- "As checker, approve."

**Tools**

| Tool | What it does |
|---|---|
| `start_case` | Opens a case. `mode` is `interactive` (you play maker and checker) or `scripted`. `live_agents` makes those agents call Claude. |
| `get_case_status`, `get_answers`, `get_fields`, `get_notifications` | Inspect the case |
| `explain_decisions` | The master's recent decisions: the state it read, rules and guardrails applied, and rationale |
| `maker_submit` | Uploads (text or `file_path`), field confirmations and screening dispositions |
| `checker_return`, `checker_approve` | Checker actions. A role is only accepted when the case is waiting on it. |
| `get_prompt`, `run_tests`, `list_scenarios` | Prompts, the test suite and available scenarios |

Each step is saved under `out/mcp/<case_id>/`. Cases are held in the server's memory, so restarting the server clears them.

## What happens in one run

```
RENEWAL_DUE ─► MASTER ─► A1 discover ─► MASTER ─┬► A2 ingest 7 docs ─► MASTER ─► A3 draft Q1-Q8 ─► MASTER
                                                 └► A5 ask maker for FY2025 FS                       │
                                                                       open items? ─► A5 notify maker┘
MAKER submits (FS + UBO confirm + screening disposition)
  ─► MASTER ─► A2 (DOC-08 only) ─► A3 redraft affected Qs ─► A4 review ─► FAIL (MEDIUM, fixable)
  ─► MASTER ─► A3 redraft Q6 ─► A4 review ─► PASS ─► A5 notify checker
CHECKER returns Q7 ─► MASTER ─► A3 suggest skeleton + A5 notify maker
MAKER uploads Seacrest register + SoW ─► A2 (2 docs) ─► A3 Q7 ─► A4 PASS ─► A5 checker
CHECKER approves ─► MASTER closes, schedules 2027-11-15 ─► A5 notify both
```

`out/sample_run.txt` has the full console output. `out/audit_log.jsonl` has every event, every master decision (with the state it read, the rules it matched and its rationale) and every agent input and output.

## How the master decides

`orchestrator/master.py` → `Master.decide(case, event, now)` follows the same procedure for every event:

1. **Wait if work is in flight.** If parallel or chained tasks are still pending, it returns `WAIT`.
2. **Guardrails** (G1–G6) are checked first and always win over routing:
   - **G1:** a field with confidence below 0.80 needs a human to confirm it.
   - **G2:** a screening hit cannot pass without a maker disposition.
   - **G3:** a HIGH finding goes back to the maker.
   - **G4:** three retries on the same item, then escalate.
   - **G5:** four-eyes control (the maker cannot be the checker).
   - **G6:** within 5 business days of the due date, every notification copies the team lead.
3. **Routing rules** R1–R9 are evaluated in order, and the first match wins.
4. **Narrow scope.** `dependency_map` (document → fields → questions) makes sure a late document re-runs only that document and the questions it affects.

Each decision is a `Decision` containing `decision`, `new_status`, `tasks[]`, `rules_matched`, `guardrails_triggered` and `rationale`.

Routing is plain code on purpose. The same state always produces the same route, and compliance can read and test it. With `--shadow`, an LLM using `prompts/master.md` makes the same decision independently and the run logs whether it agreed with the rules. That gives you a safe way to evaluate an LLM master before trusting it with routing.

## Layout

```
run.py                      CLI
mcp_server.py               MCP server for VS Code / Claude Code chat
.vscode/mcp.json, .mcp.json MCP config for Copilot agent mode / Claude Code
AGENTS.md, CLAUDE.md, .github/copilot-instructions.md   instructions for the chat agent
samples/                    maker uploads for the interactive demo
orchestrator/
  master.py                 guardrails + routing rules (the brain)
  engine.py                 event loop; the only code that writes to the case; simulated humans
  agents.py                 A1..A5: offline logic + live payload builders
  llm.py                    Claude call, JSON parse, one repair retry
  models.py                 Case, Task, Event, Decision
  report.py                 console output
prompts/                    system prompt + user template for the master and each agent
data/sample_case.json       dummy client, document store, checklist, policy, scripted maker/checker actions
tests/test_routing.py       9 routing tests
out/                        audit_log.jsonl, final_case.json, outbox.json, sample_run.txt
```

## Try a different scenario

Edit `data/sample_case.json`, for example:

- **Remove `DOC-04` (director IDs).** The master takes route R2a and sends the case to the maker before any drafting.
- **Change the shareholder register to `| 40%`.** The UBO percentages no longer sum to 100%, so Agent 4 raises a HIGH finding and G3 sends the case back to the maker.
- **Change `start_time` to `2026-11-10`.** G6 applies and the team lead is copied on every notification.
- **Change `human_script`** to give different maker submissions and checker decisions.

## Moving towards production

- **Event bus and workers.** Replace the in-memory queue with a real bus (Kafka, SQS or Pub/Sub). Run each agent as a worker that consumes task messages, and run the master as a consumer of result events.
- **Case store.** Keep the case record in a database. Use optimistic locking so that only one master decision applies per event.
- **Agent tools.** Agent 1's sources become real tools (DMS, CRM, registry and screening APIs). Agent 5 calls your mail, chat and task-inbox APIs.
- **Guardrails in code.** Keep the guardrails enforced in code even if routing later moves to an LLM.
- **Configuration.** Version the prompts, policy rules, scoring guide and dependency map as configuration, and record the version used for each decision in the audit log.

# Production backend

All data is dummy data. Commands are for Windows (Git Bash or PowerShell); on macOS/Linux use `venv/bin/python`.

## Phase 1: database and dummy data

```bash
python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt -r backend/requirements.txt

docker compose up -d --wait db                         # Postgres 16 on localhost:5432 (kyc/kyc)
cd backend
../venv/Scripts/python -m alembic upgrade head         # create the schema
../venv/Scripts/python -m kyc.seed --clients 25 --reset   # users, reference data, clients, documents, cases
../venv/Scripts/python -m pytest -q                    # generator scenarios + database four-eyes tests
```

Set `KYC_DATABASE_URL` (or copy `.env.example` to `.env`) to point somewhere else.

**What the seed creates**
- 5 users, all with password `kyc-demo-123`: makers `priya.nair`, `aaron.koh`; checkers `daniel.tan`, `siti.rahman`; admin/team lead `grace.lim`.
- Reference data from `data/sample_case.json`: checklist `SG_CORP_MED_v4`, questionnaire `CRA_CORP_v7`, rating bands, policy rules, the dependency map, and a country-risk list.
- The Halcyon Marine sample (case `RA-2026-0412`) plus N generated clients with directors, shareholders, UBOs and 7–8 documents each. The document files are written to `backend/data/generated/<CIF>/` (git-ignored), in the same text layout the agents parse.
- One open renewal case per client (status `NEW`) with an unprocessed `RENEWAL_DUE` event, ready for the worker in Phase 2.
- Scenarios rotate through `happy`, `stale_fs`, `missing_id`, `screening_hit`, `obscured_register`, `ubo_mismatch` and `near_due`. `--seed` makes the data repeatable, and `--as-of` sets the reference date.

**Data model** ([backend/kyc/db/models.py](backend/kyc/db/models.py))

| Group | Tables |
|---|---|
| Reference | `users`, `clients`, `related_parties`, `checklists`, `checklist_items`, `questionnaires`, `questions`, `rating_bands`, `country_risk`, `policy_rules`, `doc_item_fields`, `field_questions` |
| Evidence | `documents`, `extracted_fields`, `screening_hits`, `dispositions` |
| Workflow | `cases`, `case_events` (append-only, also the master's inbox), `master_decisions` (append-only), `agent_tasks` (queue claimed with `FOR UPDATE SKIP LOCKED`) |
| Output | `answers` (versioned), `review_findings`, `checker_returns`, `ratings` (versioned), `notifications` |

Four-eyes control is enforced in the database as well as by guardrail G5. A CHECK stops a case's maker and checker being the same user. Triggers require the right roles and allow only the case's assigned checker to approve a final rating.

## Next phases
2. A database-backed engine and worker, plus the FastAPI API, reusing `orchestrator/master.py` unchanged. The prototype agents are written around the Halcyon case (for example, Q4 expects both FY2024 and FY2025 turnover), so they will be generalised for the generated clients.
3. React UI: dashboard, live flow graph, maker workbench, checker review, audit replay.
4. JWT login and roles, the full stack in Docker Compose, hardening.
