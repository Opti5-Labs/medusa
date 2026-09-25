# Medusa

> Find the bug, prove it, fix it, and show your work.

IBM Bob 2.0 Hackathon entry (lablab.ai, 25–27 Sep 2026).

## Running locally

### Backend (FastAPI)

```bash
cd backend
python -m venv .venv

# Activate the virtualenv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env        # fill in WATSONX_* values
uvicorn app.main:app --reload --port 8000
```

API available at http://localhost:8000  
Interactive docs at http://localhost:8000/docs

### Frontend (Next.js)

```bash
cd frontend
npm install
cp .env.example .env.local  # already has NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

App available at http://localhost:3000

## Project structure

```
medusa/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, CORS, all stub routes
│   │   ├── config.py            # env vars and all limits in one place
│   │   └── models/contracts.py  # Pydantic data contracts (source of truth)
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx             # Landing — Run demo / Link GitHub / Upload zip
│   │   ├── issues/page.tsx      # Issue list placeholder
│   │   ├── investigate/[id]/page.tsx  # Live investigator view placeholder
│   │   └── scan/
│   │       ├── github/page.tsx  # GitHub URL entry placeholder
│   │       └── upload/page.tsx  # Zip upload entry placeholder
│   ├── lib/api.ts               # Typed fetch wrapper + EventSource helpers
│   └── .env.example
├── bob_sessions/                # Bob IDE task summary screenshots (PNG)
├── CLAUDE.md                    # Agent context and hard rules
├── PROJECT.md                   # Full plan, timeline, team split
└── README.md
```

## Data contracts

`backend/app/models/contracts.py` is the source of truth for all data shapes.  
`frontend/lib/api.ts` mirrors every type — change one, change the other in the same commit.
