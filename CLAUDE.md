# CLAUDE.md

Context for AI coding agents working in this repo. Read this before changing anything.
Full plan, timeline and team split: see `PROJECT.md`.

## What this is

**Medusa** is our entry for the IBM Bob 2.0 Hackathon (lablab.ai, 25–27 Sep 2026).
It scans a codebase for issues, reproduces them, and proposes and tests fixes.

- Submission deadline: **Sun 27 Sep 2026, 15:00 UTC (20:30 Colombo)**
- Stack: Next.js 14 (App Router) + TypeScript frontend, FastAPI (Python 3.11+) backend, Docker sandbox, Granite on watsonx.ai, IBM Bob, hosted on a single AWS EC2 instance.

Two paths through the product:

| Path           | Input                                     | Executes code?                      | Models                                                     |
| -------------- | ----------------------------------------- | ----------------------------------- | ---------------------------------------------------------- |
| OptiLearn demo | Built-in demo repo with a known, real bug | Yes, inside the Docker sandbox only | Bob (golden run replay) for investigators + recommendation |
| General repos  | Public GitHub URL or uploaded zip         | **Never**                           | Granite for scan, reproduce reasoning, and fix proposals   |

## Hard rules

1. **Scope is frozen.** Do not build: sandboxed execution on general repos, GitHub OAuth or pull-request creation, a database, user accounts. If a task seems to need one, stop and ask.
2. **Only one place runs code:** `backend/app/sandbox/runner.py`, and only for the OptiLearn image. Never call `subprocess`, `os.system`, `exec`, `eval`, `pip install`, `npm install`, or Docker on anything from a linked repo or uploaded zip. General-repo code is read as text, nothing more.
3. **No secrets anywhere in the repo.** Not in code, tests, fixtures, logs, prompts, or golden-run files. All config comes from env vars loaded from `.env` (gitignored). An exposed IBM credential gets the hackathon account deactivated.
4. **Sandbox containers get zero credentials.** Never pass `os.environ` or any `environment=` containing keys to a container.
5. **Contracts stay in sync.** `backend/app/models/contracts.py` and `frontend/lib/api.ts` describe the same shapes. Change one, change the other in the same commit.
6. **Banned watsonx models:** `llama-3-405b-instruct`, `mistral-medium-2505`, `mistral-small-3-1-24b-instruct-2503`. The model id always comes from `GRANITE_MODEL_ID`.
7. **No real personal, client, company, or social-media data** in fixtures, sample repos, or demo content.
8. **Be honest in the UI.** Reasoning-mode results are never labelled as reproduced or tested. Replayed Bob output is labelled as a recorded session.
9. **Hackathon evidence.** Core features are built in Bob IDE so they appear in `bob_sessions/`. In this repo, prefer scaffolding, tests, bug fixes and glue work unless a teammate says otherwise. Never modify or delete anything in `bob_sessions/`.

## Architecture

```
React (Vite)  ──REST + SSE──>  FastAPI
                                ├─ ingest/         GitHub tarball download or zip extract, with limits
                                ├─ pipelines/scan    → Granite (watsonx.ai) + GitHub Issues API
                                ├─ pipelines/repro   → sandboxed (OptiLearn) | reasoning (Granite)
                                ├─ pipelines/debug   → parallel sandboxed candidates (OptiLearn) | reasoning (Granite)
                                ├─ agents/bob.py     → replays golden/optilearn run (BOB_MODE=replay)
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
│   │   ├── api/                   # scan.py, health.py (repro/debug are stubs in main.py)
│   │   ├── models/contracts.py    # Pydantic data contracts (source of truth)
│   │   ├── ingest/                # github.py, zip_upload.py, safe_extract.py, limits.py
│   │   ├── pipelines/scan.py      # file selection + language detection (Granite pending)
│   │   ├── demo/                  # pre-baked OptiLearn result loader
│   │   └── github/issues.py       # read-only GitHub Issues fetch
│   ├── tests/
│   └── requirements.txt
├── sandbox/
│   └── optilearn/Dockerfile       # OptiLearn + its deps baked in at build time (not built yet)
├── frontend/
│   ├── app/                       # Next.js 14 App Router
│   │   ├── page.tsx               # Landing (Home)
│   │   ├── layout.tsx
│   │   ├── issues/page.tsx        # Issue list (shows "files selected" until Granite runs)
│   │   ├── scan/github/page.tsx   # GitHub link form
│   │   ├── scan/upload/page.tsx   # Zip upload form (limit read from NEXT_PUBLIC_MAX_ZIP_MB)
│   │   └── components/            # shared UI components
│   ├── lib/api.ts                 # fetch wrapper + typed contracts (mirrors contracts.py)
│   └── package.json
└── deploy/
    ├── nginx.conf
    └── Medusa.service           # systemd unit for the backend
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
docker build -t Medusa-optilearn:latest sandbox/optilearn

# Frontend
cd frontend
npm install
npm run dev                          # Next.js dev server (port 3000)
npm run build                        # outputs frontend/.next for nginx (next start) or static export
```

## Environment variables

Copy `.env.example` to `.env`. Never commit `.env`.

```
WATSONX_API_KEY=
WATSONX_PROJECT_ID=
WATSONX_URL=https://us-south.ml.cloud.ibm.com   # Dallas region
GRANITE_MODEL_ID=                                # confirm the id in Prompt Lab
GITHUB_TOKEN=                                    # optional; fine-grained, no scopes, raises rate limit
BOB_MODE=replay                                  # replay (default, deployed) | live (local only)
SANDBOX_IMAGE=Medusa-optilearn:latest
MAX_CONCURRENT_SANDBOXES=6
ALLOWED_ORIGIN=http://localhost:5173
```

## Data contracts

Source of truth: `backend/app/models/contracts.py`. Mirror in `frontend/src/api/types.ts`.

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
- **Rate limits** (per IP): 5 scans per 10 min, 3 repro-or-debug runs per 10 min.
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

- `BOB_MODE=replay` (default, and the only mode on the deployed server): `agents/bob.py` streams `golden/optilearn/*.jsonl` as `LogEvent`s with realistic pacing. The UI badges these as "Recorded Bob session".
- `BOB_MODE=live` (local dev only): calls Bob Shell non-interactively. Burns Bobcoins. Check the Bob Shell docs for the exact invocation; don't guess flags.
- Golden run files are reviewed for secrets and personal paths before commit.

## Granite

- All calls go through `agents/granite.py` using the `ibm-watsonx-ai` SDK.
- Prompts ask for JSON only. Validate with Pydantic. On invalid JSON, retry once, then skip that chunk with a `warn` event. A bad chunk never crashes a scan.
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
- Backend runs under systemd (`deploy/Medusa.service`) as a dedicated user. Secrets live in `/etc/Medusa/env`, never in the repo or AMI.
- nginx serves `frontend/dist` and proxies `/api` to `127.0.0.1:8000`. SSE locations need `proxy_buffering off;` and a long `proxy_read_timeout`.
- Security group: 80/443 open, 22 restricted to team IPs. Nothing else runs on this box.

## Ownership

| Area                                                                       | Owner                         |
| -------------------------------------------------------------------------- | ----------------------------- |
| `sandbox/`, `agents/bob.py`, `golden/`, investigator prompts               | Person 1 (engine)             |
| `api/`, `ingest/`, `pipelines/`, `agents/granite.py`, `github/`, contracts | Person 2 (backend)            |
| `frontend/`, `deploy/`, `bob_sessions/`, demo assets                       | Person 3 (frontend and proof) |

When a change crosses into another owner's area, keep it minimal and call it out in the commit message.
