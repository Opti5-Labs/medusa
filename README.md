# Medusa

> Find the bug, prove it, fix it, and show your work.

IBM Bob 2.0 Hackathon entry (lablab.ai, 25–27 Sep 2026). Design: [architecture.md](architecture.md).

Medusa scans a codebase for issues, reproduces a chosen issue, races candidate fixes in
parallel sandboxes and recommends one based on test evidence.

- **OptiLearn demo (verified):** a real bug in OptiLearn's Whisper fallback. The reproducer and
  every candidate fix run live in locked-down Docker containers; the recommendation is chosen
  deterministically from the results, and the fixed code can be downloaded.
- **Any public GitHub repo or zip (analysis only):** the code is read as text, issues are listed
  with file and line, one is diagnosed and patches are proposed. Nothing is executed, and the UI
  says so.

Two independent investigators, one judge:

| Role | What it does |
| --- | --- |
| IBM Bob (Bob Shell, headless) | Diagnoses the failure, proposes fixes; scans code when Granite is unavailable. Read-only, capped by `BOB_MAX_COST` / `BOB_MAX_TURNS`. |
| Granite on watsonx.ai | Scans code in chunks; runs its own runtime / repository / skeptic investigation. |
| Sandbox + `pipelines/verify.py` | Reproduces the bug and tests every fix. Decides what works; model confidence never does. |

If either model is unavailable (not configured, quota used up, auth failure, cost or turn limit),
the other carries on and the UI shows the real reason. With neither, prepared fix candidates are
used and labelled as prepared.

## Running locally

Needs Python 3.12+, Node 22.15+ and Docker. For Bob, install Bob Shell:
`curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash`

```bash
# Sandbox image (needed for the OptiLearn demo)
docker build -t medusa-optilearn:latest sandbox/optilearn

# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in IBM_WATSONX_* and BOB_API_KEY (each optional)
uvicorn app.main:app --reload --port 8000

# Frontend (second terminal)
cd frontend
npm install
cp .env.example .env.local
npm run dev                 # http://localhost:3000
```

Tests and lint:

```bash
cd backend && pytest && ruff check . && ruff format --check .   # sandbox tests skip without Docker
cd frontend && npm run typecheck && npm run build
```

## Project structure

```
medusa/
├── backend/app/
│   ├── api/            scan.py, runs.py (repro, debug, SSE, download), health.py
│   ├── pipelines/      scan.py (Granite chunks), repro.py, debug.py, verify.py, context.py
│   ├── agents/         bob.py (Bob Shell), granite.py (watsonx REST), panel.py (runs both),
│   │                   results.py (shared result shape), investigators.py, fixers.py
│   ├── sandbox/        runner.py — the only place code executes
│   ├── demo/           OptiLearn scenario and demo scan fixture
│   ├── ingest/         GitHub tarball + zip ingest with limits and safe extraction
│   └── models/contracts.py   data contracts (mirrored in frontend/lib/api.ts)
├── sandbox/optilearn/
│   ├── Dockerfile      sandbox image
│   ├── src/            OptiLearn subset at the buggy commit
│   ├── harness/        reproducer, behaviour checks, result reporter
│   └── prepared/       fallback fix candidates, used when Granite is unavailable
├── golden/optilearn/   recorded Bob run goes here (replayed when present)
├── frontend/           Next.js 16 App Router (landing, issues, investigate)
├── deploy/             nginx, systemd units, setup.sh, deploy guide
└── bob_sessions/       Bob IDE task screenshots
```

## Deploying

See [deploy/README.md](deploy/README.md): one Ubuntu EC2 instance, `deploy/setup.sh` installs everything.
