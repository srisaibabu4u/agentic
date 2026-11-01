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
- In live mode each agent works autonomously: it uses read-only tools to gather and check evidence, then submits a result that is checked against that evidence. See [Phase 5](#phase-5-autonomous-agents).
- The live loop is tested with a scripted fake client (`tests/test_agentic.py`). It has not been run against the API from this environment.

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
  agents.py                 A1..A5: offline logic + live task briefs
  toolkit.py                live agents' read-only tools and result checks
  llm.py                    Claude client: the agentic tool-use loop
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

## Phase 2: workflow on the database, and the API

```bash
cd backend
../venv/Scripts/python -m kyc.worker --until-idle    # run every seeded case until it needs a person
../venv/Scripts/python -m kyc.worker                 # or keep running, polling every second
../venv/Scripts/python -m kyc.worker --live A3,A4    # drafting and review call Claude (ANTHROPIC_API_KEY)
```

The worker repeats one unit of work at a time: a master decision on an undecided event
([services/engine.py](backend/kyc/services/engine.py)), or one agent task: claim, run, record
([services/worker.py](backend/kyc/services/worker.py)). Cases and tasks are claimed with
`FOR UPDATE SKIP LOCKED`, so several workers can run side by side. The master and agents are the prototype's own code.
[services/store.py](backend/kyc/services/store.py) rebuilds the in-memory `Case` from the tables and writes changes back,
and [orchestrator/state.py](orchestrator/state.py) holds the state-change rules both engines share.
The Halcyon run on the database matches `run.py` decision for decision (`tests/test_worker.py`).

To start over, reseed: `python -m kyc.seed --clients 25 --reset`.

**API** ([backend/kyc/api/](backend/kyc/api/)). Run it next to the worker; interactive docs are at http://localhost:8000/docs.

```bash
cd backend
../venv/Scripts/python -m uvicorn kyc.api.app:app --reload     # terminal 1
../venv/Scripts/python -m kyc.worker                           # terminal 2
TOKEN=$(curl -s -X POST localhost:8000/auth/login -H "Content-Type: application/json"   -d '{"username":"priya.nair","password":"kyc-demo-123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/cases?waiting_on_me=true"
```

| Endpoint | Who | What |
|---|---|---|
| `GET /cases` | anyone | List; filters `status`, `assigned`, `waiting_on_me`, `q` |
| `GET /cases/{ref}` | anyone | Detail: client and parties, documents, fields, answers, rating, findings, screening, notifications |
| `GET /cases/{ref}/timeline` | anyone | Every event with the master decision it led to (rules, guardrails, rationale, state read) |
| `GET /cases/{ref}/answers/{q_id}` | anyone | Every version of one answer, including suggested redrafts |
| `GET /documents/{id}` | anyone | Document text and metadata |
| `GET /cases/{ref}/stream` | anyone | Server-sent events whenever the case changes |
| `POST /cases/{ref}/maker/submit` | the case's maker | Uploads, field confirmations, dispositions |
| `POST /cases/{ref}/checker/return` | the case's checker | Return questions with comments |
| `POST /cases/{ref}/checker/approve` | the case's checker | Approve with a final rating |

Sign in with `POST /auth/login` (see Phase 4). Actions are accepted only from the case's assigned
maker or checker (otherwise `403`) and only while the case waits on that role (otherwise `409`). They append an event
and return `202`; the worker applies them.

## Phase 3: the UI

React + Vite + TypeScript in [frontend/](frontend/), with React Flow for the workflow diagram.

```bash
cd backend && ../venv/Scripts/python -m uvicorn kyc.api.app:app --reload   # terminal 1: API
cd backend && ../venv/Scripts/python -m kyc.worker                          # terminal 2: worker
cd frontend && npm install && npm run dev                                   # terminal 3: http://localhost:5173
```

Sign in as one of the seeded users (password `kyc-demo-123`; the login page lists them). Then:

- **Dashboard:** tiles for *waiting on me*, *agents working*, *due within 5 business days* and *closed*, which double as filters; search; sortable case table.
- **Case page:**
  - **Flow diagram:** every status and the master's routes (from `GET /workflow`), with the route this case took drawn in bold and labelled with its rules, and the current status lit.
  - **Live updates:** a server-sent events stream refreshes the page as the worker moves the case ("● Live").
  - **Replay:** steps or plays through the case's history; at each step it shows what the master saw when it decided.
  - **Timeline:** every event with the master's decision, rules, guardrails (hover for meaning), rationale and dispatched tasks.
  - **Summary:** client, ownership, rating, open items, documents and answers.
- **Maker workbench** (when the case waits on you as maker): confirm low-confidence values (G1), disposition screening hits next to our record (G2), upload missing or requested documents, see the checker's returns with the suggested redraft, then submit.
- **Checker review:** verify the maker's inputs (four-eyes), read each answer with evidence links that open the cited page, return questions with comments, or approve with a final rating.

End-to-end tests drive the real UI in your installed Chrome against the real API, worker and database. Postgres must be running, and the tests reseed it:

```bash
cd frontend && npm run e2e     # Halcyon from first maker review to approval, then replay (~1.5 min)
```

## Phase 4: login, one-command stack, hardening

**Run everything with Docker** (Docker Desktop or Docker Engine with Compose v2):

```bash
python -c "import secrets; print('KYC_JWT_SECRET=' + secrets.token_urlsafe(48))" > .env
docker compose up -d --build --wait
# open http://localhost:8080 and sign in, e.g. priya.nair / kyc-demo-123
```

| Service | What it does |
|---|---|
| `db` | Postgres 16 (volume `kyc-pgdata`) |
| `migrate` | Runs once: `alembic upgrade head`, then seeds dummy data if the database is empty |
| `api` | FastAPI on the internal network (not published); refuses to start without a strong `KYC_JWT_SECRET` |
| `worker` | Runs master decisions and agent tasks; scale with `docker compose up -d --scale worker=3` |
| `ui` | nginx serving the built UI on port 8080, forwarding `/api` to `api` (unbuffered, for live updates), with security headers |

`docker compose logs -f worker` follows the workflow; `docker compose down` stops it (add `-v` to delete the data).
For local development keep using `docker compose up -d --wait db` and run the API, worker and UI from source as above.

To run the end-to-end tests against a throwaway copy of the stack on other ports:

```bash
KYC_DB_PORT=55432 KYC_UI_PORT=8081 docker compose -p kyc-e2e up -d --build --wait
(cd frontend && E2E_BASE_URL=http://localhost:8081 npm run e2e)
docker compose -p kyc-e2e down -v
```

**Login.** `POST /auth/login` with a username and password (bcrypt-hashed in `users`) returns a signed JWT
(HS256, 8 hours). Browsers get it as an httpOnly, `SameSite=Strict` session cookie that scripts cannot read
and other sites cannot make the browser send; API clients send it as `Authorization: Bearer <token>`.
`POST /auth/logout` ends the session. The user's role and active flag are read from the database on every
request, so deactivating an account signs it out immediately.

| Setting | Default | Purpose |
|---|---|---|
| `KYC_JWT_SECRET` | random per start (warning) | Token signing key; set a long random value outside development |
| `KYC_TOKEN_HOURS` | `8` | Session length |
| `KYC_COOKIE_SECURE` | `true` | HTTPS-only cookie; browsers accept it on `http://localhost` |
| `KYC_ENV` | `development` | `production` refuses to start without a strong secret, and logs JSON |
| `KYC_LOGIN_MAX_FAILURES` / `KYC_LOGIN_WINDOW_MINUTES` | `5` / `15` | Failed sign-ins per username before it is locked for the window |
| `KYC_LOG_FORMAT` / `KYC_LOG_LEVEL` | `text` (`json` in production) / `INFO` | Log output |

**Hardening.**
- Failed sign-ins are stored in `login_failures`, so the per-username limit holds across API replicas.
  A locked account gets `429` with `Retry-After`, even with the right password.
- Every request gets an `X-Request-ID` (the caller's, if well-formed), returned in the response and attached to
  every log line, plus one access line per request: method, path, status, duration and user.
- In production, logs are JSON lines.
- nginx sends a strict Content Security Policy, `X-Frame-Options: DENY`, `nosniff`, `no-referrer` and a Permissions-Policy.
- The containers run as a non-root user, and the API is not published outside the Compose network.

**CI** ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs on every push and pull request:
- **`backend`:** migrations, `alembic check`, seed, prototype tests and backend tests against Postgres.
- **`frontend`:** lint, type check and build.
- **`e2e`:** Playwright in Chrome.
- **`docker`:** builds and starts the whole stack, then smoke-tests it through nginx.

## Phase 5: autonomous agents

In live mode (`--live A1,...` or `all`, on `run.py`, `kyc.worker` or the MCP `start_case`) each specialised agent
runs its own tool-use loop on Claude instead of answering one pre-built prompt:

1. **Plan.** The agent gets its system prompt, a short task brief (task envelope, case id, due date) and its tools.
2. **Gather.** It decides what to look up and calls read-only tools ([orchestrator/toolkit.py](orchestrator/toolkit.py)).
3. **Check.** It verifies its own conclusions with deterministic checking tools before relying on them.
4. **Submit.** It calls `submit_result`. The result is checked against the evidence. Any errors go back to the agent
   as a tool error, and it fixes them and resubmits.

| Agent | Tools | What a submission must pass |
|---|---|---|
| A1 Discovery | `get_checklist`, `search_dms`, `fetch_registry`, `run_screening`, `search_crm`, `check_freshness` | every checklist item found or missing; doc ids exist; `fresh` agrees with the freshness rule; stale items listed as missing |
| A2 Ingestion | `list_documents`, `read_document`, `find_in_document`, `get_field_schema` | field ids in the schema; source is `<doc in scope>#p<page>`; `raw` is quoted exactly from that document; confidence < 0.80 is flagged |
| A3 Drafting | `get_questions`, `get_notes_to_resolve`, `list_fields`, `get_field`, `get_open_items`, `lookup_country_risk`, `lookup_industry_risk`, `compute_rating`, … | exactly the questions in scope; citations exist; scores 1–5 or null with `NEEDS_HUMAN`; open items flagged |
| A4 Review | `get_answers_in_scope`, `check_citations`, `check_ubo_sum`, `recompute_rating`, `get_policy_rules`, … | valid checks and severities; verdict consistent with the findings; recomputed rating correct |
| A5 Notification | `get_recipients`, `get_case_summary`, `get_action_items` | one message per recipient in order; case id, link and every action item in the body |

**What stays fixed.** Agents still never write to the case and never call each other. The engine applies their
results and the deterministic master routes (R1–R9, G1–G6). An agent that does not submit an accepted result
within its turn budget returns `FAILED`, and the retry guardrail G4 takes over. Every tool call, rejected
submission and token count is kept in the task result's `agent_trace` (`agent_tasks.result` in the database).

| Setting | Default | Purpose |
|---|---|---|
| `KYC_MODEL` | `claude-sonnet-5-5` | Model for every live agent |
| `KYC_EFFORT` | `medium` | Reasoning effort (`low` … `max`) |
| `KYC_MAX_TURNS` | `20` | Turn budget per agent task |

Requests opt in to server-side refusal fallbacks (`fallbacks: "default"`). The offline agents are unchanged, and
tests check that every deterministic result also passes the live result checks, across every seeded scenario.
