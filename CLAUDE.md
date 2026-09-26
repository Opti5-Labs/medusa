# CLAUDE.md

Context for AI coding agents working in this repo. Read this before changing anything.
Full plan, timeline and team split: see `PROJECT.md`.

## What this is

**Medusa** is our entry for the IBM Bob 2.0 Hackathon (lablab.ai, 25–27 Sep 2026).
It scans a codebase for issues, reproduces them, and proposes and tests fixes.

- Submission deadline: **Sun 27 Sep 2026, 15:00 UTC (20:30 Colombo)**
- Stack: Next.js 16 (App Router, React 19, Tailwind 4) + TypeScript frontend, FastAPI (Python 3.12+) backend, Docker sandbox, Granite on watsonx.ai, IBM Bob, hosted on a single AWS EC2 instance.

Two paths through the product:

| Path           | Input                                     | Executes code?                      | Models                                                     |
| -------------- | ----------------------------------------- | ----------------------------------- | ---------------------------------------------------------- |
| OptiLearn demo | Built-in demo repo with a known, real bug | Yes, inside the Docker sandbox only | Bob + Granite as independent investigators; sandbox verifies  |
| General repos  | Public GitHub URL or uploaded zip         | **Never**                           | Granite scan; Bob + Granite diagnose and propose (unverified) |

## Hard rules

1. **Scope is frozen.** Do not build: sandboxed execution on general repos, GitHub OAuth or pull-request creation, a database, user accounts. If a task seems to need one, stop and ask.
2. **Only one place runs code:** `backend/app/sandbox/runner.py`, and only for the OptiLearn image. Never call `subprocess`, `os.system`, `exec`, `eval`, `pip install`, `npm install`, or Docker on anything from a linked repo or uploaded zip. General-repo code is read as text, nothing more. The one other subprocess is `agents/bob.py` starting the Bob Shell CLI (our tool, not repo code) in ask mode with the `edit`/`execute` tool groups disabled, on a throwaway copy of the files, with only `PATH`, `HOME` and `BOB_API_KEY` in its environment.
3. **No secrets anywhere in the repo.** Not in code, tests, fixtures, logs, prompts, or golden-run files. All config comes from env vars loaded from `.env` (gitignored). An exposed IBM credential gets the hackathon account deactivated.
4. **Sandbox containers get zero credentials.** Never pass `os.environ` or any `environment=` containing keys to a container.
5. **Contracts stay in sync.** `backend/app/models/contracts.py` and `frontend/lib/api.ts` describe the same shapes. Change one, change the other in the same commit.
6. **Banned watsonx models:** `llama-3-405b-instruct`, `mistral-medium-2505`, `mistral-small-3-1-24b-instruct-2503`. The model id always comes from `GRANITE_MODEL_ID`.
7. **No real personal, client, company, or social-media data** in fixtures, sample repos, or demo content.
8. **Be honest in the UI.** Reasoning-mode results are never labelled as reproduced or tested. Replayed Bob output is labelled as a recorded session.
9. **Hackathon evidence.** Core features are built in Bob IDE so they appear in `bob_sessions/`. In this repo, prefer scaffolding, tests, bug fixes and glue work unless a teammate says otherwise. Never modify or delete anything in `bob_sessions/`.

## Architecture

```
Next.js       ──REST + SSE──>  FastAPI
                                ├─ ingest/         GitHub tarball download or zip extract, with limits
                                ├─ pipelines/scan    → Granite (watsonx.ai) + GitHub Issues API
                                ├─ pipelines/repro   → sandboxed (OptiLearn) | reasoning (Granite)
                                ├─ pipelines/debug   → parallel sandboxed candidates (OptiLearn) | reasoning (Granite)
                                ├─ pipelines/verify  → deterministic pass/fail + ranking (no LLM)
                                ├─ agents/bob.py     → replays golden/optilearn run (BOB_MODE=replay) when present
                                └─ sandbox/runner.py → Docker SDK, one container per attempt
```

Nothing persists between sessions. Run state lives in an in-memory store plus a per-run temp directory, both deleted when the run ends or after a TTL (30 min).

## Repo layout

```
.
├── CLAUDE.md
├── PROJECT.md
├── README.md
├── .env.example
├── bob_sessions/                  # Bob IDE task summary screenshots (PNG). Do not touch.
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI app, routers, CORS, rate limiting
│   │   ├── config.py              # env loading (load_dotenv) + all limits as constants
│   │   ├── store.py               # in-memory run store with TTL cleanup
│   │   ├── errors.py              # MedusaError + global handler
│   │   ├── ratelimit.py           # per-IP sliding-window rate limiter
│   │   ├── api/                   # scan.py, runs.py (repro/debug/SSE/download), health.py
│   │   ├── models/contracts.py    # Pydantic data contracts (source of truth)
│   │   ├── ingest/                # github.py, zip_upload.py, safe_extract.py, limits.py
│   │   ├── pipelines/             # scan.py, repro.py, debug.py, verify.py, context.py
│   │   ├── agents/                # granite.py, investigators.py, fixers.py, bob.py
│   │   ├── sandbox/runner.py      # the only execution entry point
│   │   ├── demo/                  # OptiLearn scenario (optilearn.py) + demo scan fixture
│   │   └── github/issues.py       # read-only GitHub Issues fetch
│   ├── tests/
│   └── requirements.txt
├── sandbox/
│   └── optilearn/                 # Dockerfile, src/ (OptiLearn subset), harness/ (reproducer), prepared/ (fallback fixes)
├── frontend/
│   ├── app/                       # Next.js 16 App Router
│   │   ├── page.tsx               # Landing (Home)
│   │   ├── layout.tsx
│   │   ├── issues/page.tsx        # Issue list with Reproduce / Debug actions
│   │   ├── investigate/[id]/page.tsx  # Reproduce panel + debug race
│   │   ├── scan/github/page.tsx   # GitHub link form
│   │   ├── scan/upload/page.tsx   # Zip upload form (limit read from NEXT_PUBLIC_MAX_ZIP_MB)
│   │   └── components/            # shared UI components
│   ├── lib/api.ts                 # fetch wrapper + typed contracts (mirrors contracts.py)
│   └── package.json
└── deploy/
    ├── nginx.conf
    ├── medusa-backend.service   # systemd unit for FastAPI
    ├── medusa-frontend.service  # systemd unit for next start
    └── setup.sh                 # idempotent Ubuntu bootstrap
```

## Commands

```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
pytest
ruff check . && ruff format .

# Sandbox image (needed for the OptiLearn path)
docker build -t medusa-optilearn:latest sandbox/optilearn

# Frontend
cd frontend
npm install
npm run dev                          # Next.js dev server (port 3000)
npm run build                        # outputs frontend/.next, served by `next start` behind nginx
```

## Environment variables

Copy `.env.example` to `.env`. Never commit `.env`.

```
IBM_WATSONX_API_KEY=                             # WATSONX_* names also accepted
IBM_WATSONX_PROJECT_ID=
IBM_WATSONX_URL=https://eu-de.ml.cloud.ibm.com   # our project is in Frankfurt
IBM_WATSONX_MODEL=ibm/granite-4-h-small          # GRANITE_MODEL_ID also accepted
GITHUB_TOKEN=                                    # optional; fine-grained, no scopes, raises rate limit
BOB_MODE=live                                    # live (default) | replay | off
BOB_API_KEY=                                     # Inference-scoped, backend only
BOB_MAX_COST=0.25                                # Bobcoins per investigation
BOB_MAX_TURNS=6
SANDBOX_IMAGE=medusa-optilearn:latest
MAX_CONCURRENT_SANDBOXES=6
ALLOWED_ORIGIN=http://localhost:3000
TRUST_FORWARDED_FOR=false                        # true only behind deploy/nginx.conf
```

## Data contracts

Source of truth: `backend/app/models/contracts.py`. Mirror in `frontend/lib/api.ts`. The block below is a summary; the file has the full set (TestResults, PatchStats, DebugDone, origin/investigator labels).

```python
from typing import Literal
from pydantic import BaseModel

Priority = Literal["Low", "Medium", "High"]
Mode = Literal["sandboxed", "reasoning"]

class Issue(BaseModel):
    id: str                      # uuid, unique across all scans
    title: str
    description: str
    priority: Priority
    source: Literal["scan", "github_issue"]
    category: Literal["security", "correctness", "performance", "maintainability"] | None = None
    file: str | None = None
    function: str | None = None
    github_url: str | None = None

class ScanResult(BaseModel):
    scan_id: str
    repo_source: Literal["demo", "github", "zip"]
    language: str
    files_scanned: list[str]     # paths actually analysed, shown to the user
    files_total: int
    issues: list[Issue]
    warnings: list[str] = []     # e.g. "scan capped at 40 files"

class LogEvent(BaseModel):
    ts: float
    source: str                  # "investigator:1", "synthesis", "sandbox", "candidate:c2", "granite"
    level: Literal["info", "warn", "error", "result"]
    message: str

class ReproAttempt(BaseModel):
    attempt_id: str
    issue_id: str
    mode: Mode
    status: Literal["running", "reproduced", "not_reproducible", "plausible", "error"]
    log: list[LogEvent] = []
    root_cause: str | None = None
    confidence: float | None = None   # reasoning mode only

class FixAttempt(BaseModel):
    candidate_id: str
    approach: str
    patch: str | None = None          # unified diff
    sandbox_status: Literal["running", "passed", "failed", "not_applicable"]
    test_results: dict | None = None  # {"passed": int, "failed": int, "total": int}
    active: bool = True

class DebugSession(BaseModel):
    session_id: str
    issue_id: str
    mode: Mode
    candidates: list[FixAttempt]

class Recommendation(BaseModel):
    candidate_id: str
    reason: str
```

Status rules:

- `reproduced` and `not_reproducible` only in `sandboxed` mode.
- `plausible` only in `reasoning` mode, always with `confidence`.
- In `reasoning` mode every `FixAttempt.sandbox_status` is `not_applicable`.
- `error` covers timeouts, sandbox failures and model failures. It always has a visible `LogEvent` with level `error`.

## API

| Method | Path                                             | Body                                               | Returns                                                                                                |
| ------ | ------------------------------------------------ | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| GET    | `/api/health`                                    |                                                    | `{"ok": true}`                                                                                         |
| POST   | `/api/scan`                                      | `{"source": "demo" \| "github", "repo_url"?: str}` | `ScanResult`                                                                                           |
| POST   | `/api/scan/upload`                               | multipart `file` (zip)                             | `ScanResult`                                                                                           |
| POST   | `/api/issues/{issue_id}/repro`                   |                                                    | `ReproAttempt` (status `running`)                                                                      |
| GET    | `/api/repro/{attempt_id}/events`                 |                                                    | SSE stream of `LogEvent`, then a `done` event with the final `ReproAttempt`                            |
| POST   | `/api/issues/{issue_id}/debug`                   | `{"candidates": 2..6}`                             | `DebugSession`                                                                                         |
| GET    | `/api/debug/{session_id}/events`                 |                                                    | SSE stream of `LogEvent` (source `candidate:<id>`), then `done` with `DebugSession` + `Recommendation` |
| GET    | `/api/debug/{session_id}/download?candidate_id=` |                                                    | `application/zip` with the fix applied                                                                 |

"Reproduce and Debug" is the frontend calling repro, waiting for `done`, then calling debug.
General repos default to 2 debug candidates; OptiLearn allows 2–6.

## Limits

All live in `app/config.py`. Change them there, nowhere else.

- **Zip upload:** 20 MB max, 2,000 files max after extraction. Reject symlinks, absolute paths and any path that resolves outside the extract dir (zip slip).
- **GitHub repo:** public only. Check `size` from `GET /repos/{owner}/{repo}` before downloading (reject over 50 MB). Download via the tarball endpoint; no `git` on the server.
- **Scan cap:** 40 files and 6,000 lines total. Skip `node_modules`, `dist`, `build`, `.git`, `venv`, vendored code, lockfiles, binaries and minified files. Report what was skipped in `warnings`.
- **Rate limits** (per IP): 5 scans per 10 min, 6 repro-or-debug runs per 10 min (3 full Reproduce-and-Debug flows).
- **Timeouts:** scan 90 s total, sandbox run 90 s wall clock, Granite call 30 s with one retry.
- Oversize or rate-limited requests return a plain, specific message the UI shows as-is (e.g. suggest a smaller repo or a subdirectory).

## Sandbox

`sandbox/runner.py` is the only execution entry point. Every container is created with:

- `network_disabled=True` (OptiLearn deps are baked into the image, so no network is ever needed)
- `mem_limit="512m"`, `nano_cpus=500_000_000` (0.5 CPU), `pids_limit=256`
- `read_only=True` with a small `tmpfs` at `/tmp` and `/work`
- `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`, non-root `user`
- no environment variables, no host mounts except a read-only copy of the code under test
- killed at the wall-clock timeout, always removed afterwards (in a `finally`)
- a global `asyncio.Semaphore(MAX_CONCURRENT_SANDBOXES)` across all runs

A candidate whose patch won't apply or whose run fails shows `failed` in its own panel. It never stops the other candidates or the session.

## Bob

Bob and Granite are **independent investigators** (`agents/panel.py`). Both get the same inputs (issue, relevant code, and on the demo path the sandbox's runtime evidence), run in parallel, never see each other's diagnosis, and return the same normalised `InvestigatorResult` (`agents/results.py`). Their confidence is displayed, never used to rank. Their proposed fixes go through the same sandbox race; `pipelines/verify.py` decides.

- `BOB_MODE=live` (default): `agents/bob.py` runs `bob run --format json --mode ask` with `--max-cost $BOB_MAX_COST` (0.25) and `--max-turns $BOB_MAX_TURNS` (6), `--disable-tool-groups edit,execute,mcp,subagent,skill`, a wall-clock timeout, and a 6-hour cache of successful answers. The key is an Inference-scoped `BOB_API_KEY` (no `--team-id`), backend only.
- `BOB_MODE=replay`: streams `golden/optilearn/investigation.jsonl` (format in `golden/optilearn/README.md`) as a "Recorded Bob session". `BOB_MODE=off`: Bob is not used.
- Spend: every live run also draws from a server-wide daily budget (`BOB_DAILY_BUDGET`, default 5 Bobcoins per UTC day, `agents/bob_budget.py`). A run reserves `BOB_MAX_COST` before starting and settles its real cost after, so concurrent runs cannot overshoot; the day's total survives restarts in `BOB_BUDGET_FILE`. Cached answers are free. When it is used up, Bob reports so and resets at 00:00 UTC.
- Degradation: both available → both; one unavailable → the other, with the reason shown; both unavailable → prepared candidates, labelled. Bob failures (not installed, no key, auth, turn/cost limit, timeout, unusable answer) are always surfaced with the real reason, never silently replaced.
- Scan: Granite reviews the code in chunks. If it cannot analyse any of it (not configured, quota used up, not authorised), Bob reviews the selected files in one read-only run instead (up to 40k characters, same cost/turn caps). Every scan issue records `found_by` ("granite" | "bob"); when both are down the notice names both reasons.
- Debug slots: Bob's own fixes take up to half; Granite fills the rest from its own diagnosis; prepared only for slots neither filled. Only Granite candidates get the revision round (it costs no Bobcoins).
- Tests never call live Bob: `tests/conftest.py` blanks `BOB_API_KEY` and points `BOB_BINARY` at nothing. Bob tests use a fake `bob` executable.

## Granite

- All calls go through `agents/granite.py`, which calls the watsonx.ai REST API (IAM token + `/ml/v1/text/chat`) with httpx.
- Prompts ask for JSON only. Validate with Pydantic. On invalid JSON, retry once, then skip that chunk with a `warn` event. A bad chunk never crashes a scan.
- Response schemas subclass `LenientModel` (absorbs list-vs-string drift). 429s back off and retry; `token_quota_reached` and auth failures are not retried and are shown as-is.
- Identical prompts are served from a 6-hour cache in `granite.py`. Keep prompts small: send the relevant excerpt, not whole files. The watsonx.ai project has a token quota; one full demo flow used to cost ~80k tokens.
- Chunk by file (split files over 400 lines by top-level definitions). Run chunks concurrently, max 5 in flight.

## Conventions

- Python: type hints everywhere, async I/O, Pydantic v2, `ruff` for lint and format.
- SSE via `sse-starlette`. Any operation that can exceed a few seconds streams.
- Frontend: function components and hooks, `useReducer` for panel state, no global state library. `EventSource` for streams, closed on unmount.
- Errors are shown, never swallowed: every failure ends in a visible `error` event with a human-readable message.
- Keep functions small and files focused; one pipeline per file.
- Tests to keep green: zip-slip and size limits, contract round-trips, scan cap, replay stream, sandbox runner (skip when Docker is unavailable).

## Deploy (AWS)

- One EC2 instance (Ubuntu, 4 vCPU / 16 GB, e.g. `t3.xlarge`) with Docker installed and the sandbox image pre-built.
- Backend and frontend run under systemd as the dedicated `medusa` user. Secrets live in `/etc/Medusa/env`, never in the repo or AMI.
- nginx proxies `/` to `next start` on `127.0.0.1:3000` and `/api` to `127.0.0.1:8000`. SSE locations need `proxy_buffering off;` and a long `proxy_read_timeout`. Everything is in `deploy/` (`setup.sh`, `nginx.conf`, `medusa-backend.service`, `medusa-frontend.service`, `README.md`). The backend must run a single worker: run state is in memory.
- Security group: 80/443 open, 22 restricted to team IPs. Nothing else runs on this box.

## Ownership

| Area                                                                       | Owner                         |
| -------------------------------------------------------------------------- | ----------------------------- |
| `sandbox/`, `agents/bob.py`, `golden/`, investigator prompts               | Person 1 (engine)             |
| `api/`, `ingest/`, `pipelines/`, `agents/granite.py`, `github/`, contracts | Person 2 (backend)            |
| `frontend/`, `deploy/`, `bob_sessions/`, demo assets                       | Person 3 (frontend and proof) |

When a change crosses into another owner's area, keep it minimal and call it out in the commit message.
