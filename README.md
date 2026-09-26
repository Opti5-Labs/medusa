# Medusa

> Find the bug, prove it, fix it, and show your work.

IBM Bob 2.0 Hackathon entry (lablab.ai, 25–27 Sep 2026). Design: [architecture.md](architecture.md).

Medusa scans a codebase for issues, reproduces a chosen issue, races several candidate
fixes in parallel sandboxes and recommends one based on test evidence.

- **OptiLearn demo (verified):** a real bug in OptiLearn's Whisper fallback. The reproducer and
  every candidate fix run live in locked-down Docker containers; the recommendation is chosen
  deterministically from the results, and the fixed code can be downloaded.
- **Any public GitHub repo or zip (analysis only):** Granite reads the code as text, lists issues,
  diagnoses one with file/line citations and proposes patches. Nothing is executed, and the UI
  says so.

## Running locally

Needs Python 3.12+, Node 22+ and Docker.

```bash
# Sandbox image (needed for the OptiLearn demo)
docker build -t medusa-optilearn:latest sandbox/optilearn

# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in IBM_WATSONX_* (optional: without it, no Granite features)
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
│   ├── agents/         granite.py (watsonx REST), investigators.py, fixers.py, bob.py (replay)
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
