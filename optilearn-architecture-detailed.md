# OptiLearn Architecture

**Version 1.1.0** · Python 3.11 · FastAPI · React 18 · SQLite · Ollama · FAISS · Electron

---

## What Is This Document

This is an orientation guide for contributors. Its goal is to answer one question before you touch any code: _where does X live, and why is it there?_

It does not replace code comments, docstrings, or the README. It is not a spec. Treat it as a country map, not a street atlas — enough to orient you, not enough to be a liability when the code moves.

---

## The Problem This Project Solves

Refugee camps and disaster-recovery classrooms have students but no reliable internet. Commercial ed-tech requires cloud accounts, subscriptions, and always-on connectivity. OptiLearn runs entirely on a teacher's laptop, serves 30+ student browsers over a local Wi-Fi hotspot, and requires no internet connection to function. When internet is available it can optionally route certain requests to larger cloud models.

Every architectural decision flows from that constraint: **offline first, zero external dependencies at runtime**.

---

## Bird's-Eye View

```mermaid
graph TB
    subgraph Desktop["Desktop App  (optional Electron wrapper)"]
        EL["Electron main.js\nSetup wizard · server spawn · tray"]
    end

    subgraph Laptop["Teacher Laptop"]
        subgraph Server["OptiLearn Server  :8000 / :8443"]
            SPA["React SPA\nVite + PWA\ndist/"]
            API["FastAPI\n19 routers"]
            Ollama["Ollama\nGemma 4 E2B / E4B\n:11434"]
            DB["SQLite WAL\n11 tables"]
            FAISS["FAISS\nMiniLM-L12-v2"]
            Voice["Whisper ASR\nPiper + MMS-TTS"]
            Net["Captive Portal DNS :53\nmDNS  ·  HTTPS :8443"]
        end
    end

    subgraph Devices["Student Devices  (Wi-Fi hotspot)"]
        S1["Phone"]
        S2["Tablet"]
        S3["Laptop PWA"]
    end

    subgraph Cloud["Optional Cloud"]
        G["Gemini API"]
        G26["Gemma 4 26B\ntranslation"]
        BP["Beyond Presence\navatar"]
    end

    EL -->|spawns| Server
    S1 & S2 & S3 -->|HTTP :8000| API
    S1 & S2 & S3 -->|HTTPS :8443 mic| API
    Net -.->|captive redirect| S1
    SPA <--> API
    API --> Ollama & DB & FAISS & Voice
    API -.->|auto mode only| G & G26 & BP
```

The React SPA is built once (`npm run build`) and served as static files by the same FastAPI process. There is no separate frontend server in production. The single Uvicorn process handles everything.

The **desktop app** (`desktop/`) is a pure wrapper — Electron spawns the same FastAPI server as a child process and opens a native window pointed at `localhost:8000`. The web app code is untouched.

---

## Desktop App Layer

```
desktop/
├── electron/
│   ├── main.js           Main process. Owns the full app lifecycle:
│   │                       1. First launch → show setup wizard window
│   │                       2. Subsequent launches → show loading screen
│   │                       3. Spawn FastAPI server (bundled Python runtime)
│   │                       4. Poll :8000 until ready, then open main window
│   │                       5. Mac: hide to tray on close; Windows: quit
│   ├── setup.html        First-launch setup wizard (pure HTML/CSS/JS).
│   │                       Step 1: API keys (Gemini, Beyond Presence) or offline toggle
│   │                       Step 2: Ollama install check + download link
│   │                       Step 3: AI model pull with live progress bar
│   ├── preload.js        Minimal context bridge for the main app window.
│   └── preload-setup.js  IPC bridge exposed to setup.html:
│                           saveEnv · checkOllama · openOllamaDownload
│                           pullModel · launchApp · onPullProgress
└── scripts/
    ├── build.sh          Builds frontend → bundles Python → runs electron-builder.
    │                     Uses /usr/bin/python3 (system Python) for dmg-builder
    │                     to avoid Homebrew libexpat ABI mismatch.
    ├── bundle_python.py  Copies .venv/bin + .venv/lib into desktop/python-runtime/.
    └── dev.sh            Dev mode: uses optilearn/.venv directly, no bundling.
```

**Setup wizard flow**

```
First launch?
  YES → setup.html opens as a separate BrowserWindow
        Step 1: saves API keys to $userData/.env via IPC
        Step 2: pings localhost:11434 — offers ollama.com download if missing
        Step 3: streams `ollama pull <model>` output to progress bar
        Done  → writes $userData/.setup-done → calls launchApp()
  NO  → loading screen → FastAPI spawn → main window
```

**ENV_FILE handoff**

The server is spawned with `ENV_FILE=$userData/.env` in its environment. `config.py` reads this variable at import time and passes it to pydantic-settings as the `env_file` path. Web/dev mode leaves `ENV_FILE` unset so pydantic-settings falls back to `.env` in cwd — identical behaviour to before.

---

## Codemap

This section answers "where does X live?" for the most common starting points.

### Backend

```
optilearn/app/
├── main.py               Entry point. App factory, lifespan startup, middleware, router mounts, SPA catch-all.
│
├── api/
│   ├── sse.py            One function: format a dict as an SSE data line. Used by every streaming route.
│   └── routes/           One file per domain. Each owns its HTTP layer only — no business logic.
│       ├── chat.py         Core tutor chat. The most important file. Owns the SSE tool-dispatch loop.
│       ├── live_class.py   Real-time audio translation pipeline (teacher → students).
│       ├── live_quiz.py    Kahoot-style quiz game engine.
│       ├── translate.py    Document translation + notes generation.
│       ├── feature1.py     Material explain/ask/translate + TTS router (tts_router lives here).
│       ├── auth.py         Student + teacher + admin authentication. JWT for teachers, PIN for students.
│       ├── students.py     Student CRUD, progress, export/import.
│       ├── teacher.py      Teacher dashboard data: roster, heatmap, alerts, schedule.
│       ├── teacher_quiz.py Quiz builder (teacher) and quiz list (student). Two routers in one file.
│       ├── materials.py    Material upload, listing, FAISS reindex trigger.
│       ├── sessions.py     Chat session lifecycle.
│       ├── persona.py      Beyond Presence avatar chat. Returns bey.chat/{agent_id} URL directly.
│       ├── dashboard.py    Aggregated teacher analytics.
│       ├── network.py      Hotspot IP detection, QR code, captive portal status.
│       └── settings.py     Network mode toggle + API key management (GET/POST /api/settings/api-keys).
│
├── core/
│   ├── config.py         Single source of truth for all settings. Reads ENV_FILE env var for desktop mode,
│   │                     falls back to .env in cwd for web/dev mode.
│   ├── prompts.py        Builds the system prompt per student. Injects mastery data here.
│   ├── languages.py      Supported language list (ISO 639-1 codes + display names).
│   ├── grades.py         Grade level normalisation helpers.
│   └── text_formatting.py  Text cleanup utilities.
│
├── models/
│   └── schemas.py        All Pydantic request/response models. Read this before touching any route.
│
├── services/             Business logic. Routes call services; services never import routes.
│   ├── db.py               Every SQLite operation. WAL mode. The only file that touches the DB.
│   ├── model_client.py     AI routing abstraction. Decides Ollama vs Gemini vs 26B. The routing brain.
│   ├── model_scheduler.py  6-lane priority semaphore queue. Prevents live translation starvation.
│   ├── context_prep.py     Trims chat history to fit the context budget.
│   ├── faiss_store.py      Vector similarity search over curriculum.
│   ├── whisper_client.py   ASR: whisper.cpp binary first, HF transformers fallback.
│   ├── tts_client.py       TTS: Piper (14 langs) + MMS-TTS (Tamil, Somali, Amharic, Bengali, Urdu).
│   ├── student_transfer.py Encrypted ZIP export/import (pyzipper AES-256).
│   ├── job_manager.py      In-memory background job tracking.
│   ├── telemetry.py        AI request tracing and latency metrics.
│   ├── generated_cache.py  Output cache for quiz / report / translation results.
│   ├── client_tracker.py   Tracks student devices connected to the hotspot.
│   ├── network.py          Detects the hotspot IP via psutil network interface scan.
│   ├── dns_server.py       Captive portal DNS server (dnslib, UDP :53).
│   └── mdns_server.py      mDNS service advertisement via zeroconf.
│
└── tools/
    └── agent_tools.py    Four agent tools + TOOL_SCHEMAS list. Called by the model during chat.
```

### Frontend

```
optilearn/frontend/src/
├── App.jsx               Root: QueryClientProvider → AuthProvider → BrowserRouter → NetworkGate → Routes.
├── main.jsx              React DOM entry. Mounts App.
│
├── api/client.js         All fetch calls in one place. Import these in screens/hooks, not raw fetch().
│
├── context/
│   ├── AuthContext.jsx   Global auth state. studentId and teacherToken live here, persisted to localStorage.
│   └── ThemeContext.jsx  Theme toggle including dyslexia-friendly font.
│
├── hooks/
│   ├── useSSE.js           Generic EventSource consumer. Used by StudentSession and LiveTranslator.
│   ├── useLiveQuizSocket.js  SSE consumer for the live quiz game state.
│   └── useDashboard.js     Teacher dashboard data via react-query.
│
├── screens/              Page-level components. One file per route.
│   ├── StudentSession.jsx    The core student experience. Renders the SSE chat stream.
│   ├── TeacherDashboard.jsx  Teacher root. Hosts MaterialUpload, TeacherQuizBuilder,
│   │                         LiveClassTeacherPanel, DiagnosticsPanel as embedded views.
│   ├── LiveTranslator.jsx    Student-side real-time translation listener.
│   ├── livequiz/             Four screens for the live quiz game flow.
│   └── ...                   Other LMS screens (courses, grades, calendar, etc.).
│
├── components/           Reusable UI. Never fetch data here — receive it as props.
│   ├── StudentLayout.jsx   Sidebar navigation wrapper for all student/:id/* routes.
│   ├── ChatMessage.jsx     Renders one message: Markdown, LaTeX, code blocks.
│   ├── FormattedText.jsx   Mixed Markdown + LaTeX renderer used by ChatMessage.
│   ├── TopicHeatmap.jsx    Teacher analytics: topic × student mastery grid.
│   └── ...                 15 more components (badges, modals, pickers, spinners).
│
├── utils/
│   ├── audioCapture.js   MediaRecorder wrapper for microphone input.
│   └── personaIcons.jsx  Avatar icon map for persona picker.
│
└── pwaInstall.js         PWA install prompt logic (wraps the beforeinstallprompt event).
```

---

## Architecture Invariants

These are the constraints you must not break. They are not documented in code because code does not enforce absence.

**1. Routes do not touch the database directly.**  
All SQLite access goes through `services/db.py`. Routes call service functions. If you find a route importing `aiosqlite` directly, that is a bug.

**2. `tutor_fast` profile never routes to cloud.**  
`MODEL_PROFILES["tutor_fast"]` has `use_cloud_when_auto = False`. Student chat always stays local regardless of internet availability. Cloud costs and latency are unacceptable for the core tutoring loop.

**3. `ensure_ascii=False` in every SSE payload.**  
The shared `sse()` function in `app/api/sse.py` uses `json.dumps(event, ensure_ascii=False)`. Arabic, Amharic, Tamil tokens would otherwise inflate 3–6× per character. Do not inline `json.dumps` in a route without this flag.

**4. Services never import routes.**  
The dependency arrow is one-way: `routes → services → db`. Circular imports here will cause FastAPI startup failures that are painful to debug.

**5. `db.py` is the only file that holds a database connection.**  
All other files call async functions in `db.py`. This makes the WAL pragma and foreign key enforcement happen in exactly one place.

**6. The frontend never reads auth state from anywhere except `AuthContext`.**  
`teacherToken` and `studentId` are stored in localStorage and read exclusively through `AuthContext.jsx`. Do not read `localStorage` directly in screens or components.

---

## Key Data Flows

### Student Chat (the critical path)

```mermaid
sequenceDiagram
    participant B as Browser
    participant F as chat.py
    participant DB as db.py
    participant S as model_scheduler
    participant AI as Ollama

    B->>F: POST /api/chat
    F->>DB: get_student + get_mastery + get_history
    F->>F: build_system_prompt (mastery injected here)

    alt message contains tool keyword
        F->>AI: complete_with_tools [12s timeout]
        AI-->>F: ToolCallEvent
        F->>F: _dispatch_tool → agent_tools.py
        F-->>B: SSE tool_start / tool_done
    end

    F->>S: stream_with_lane(student_chat)
    S->>AI: POST /api/chat (Ollama)
    loop tokens
        AI-->>B: SSE {type: token}
    end
    F-->>B: SSE {type: done}
    F->>DB: add_message + increment_message_count
```

### AI Model Routing

```mermaid
flowchart TD
    A(["AI request with profile"]) --> B["load_user_network_settings()"]
    B --> C{mode?}

    C -->|offline| OLLAMA1(["Ollama  always"])
    C -->|online| CLOUD1(["Cloud API  always"])
    C -->|auto| D["check_connectivity()\nGET https://dns.google"]

    D --> E{reachable?}
    E -->|No| OLLAMA2(["Ollama"])
    E -->|Yes| F{profile\nuse_cloud_when_auto?}

    F -->|No| OLLAMA3(["Ollama"])
    F -->|Yes| G{route_type?}

    G -->|TUTOR| OLLAMA4(["Ollama  tutor always local"])
    G -->|TRANSLATION| G26B(["Gemma 4 26B API"])
    G -->|ADMIN| GEMINI(["Gemini API"])
```

### Live Class Translation

```mermaid
sequenceDiagram
    participant T as Teacher Browser
    participant F as live_class.py
    participant W as whisper_client
    participant AI as Ollama / 26B
    participant S as Student Browsers

    T->>F: POST /live-class/start
    F-->>T: session_id

    loop audio chunks
        T->>F: POST /live-class/{id}/audio-chunk
        F->>W: transcribe_audio()
        W-->>F: transcript
        F->>AI: translate [translation_fast profile]
        AI-->>F: translated text
        F-->>T: SSE teacher-stream transcript
        F-->>S: SSE student-stream translation
    end

    T->>F: POST /live-class/{id}/end
    F->>AI: generate_notes [notes_balanced]
    F->>F: save class_notes record
```

---

## Scheduler Lanes

Every AI request goes through a named lane. The lane determines concurrency and timeout. This prevents a quiz generation from blocking a live translation.

```mermaid
graph LR
    subgraph Lanes["model_scheduler.py  —  asyncio.Semaphore per lane"]
        L1["live_translation\nconcurrency 1  ·  180 s"]
        L2["live_class_translation\nconcurrency 3  ·  120 s"]
        L3["student_chat\nconcurrency 1  ·  240 s"]
        L4["live_class_transcription\nconcurrency 1  ·  60 s"]
        L5["admin\nconcurrency 1  ·  300 s"]
        L6["background\nconcurrency 1  ·  600 s"]
    end

    REQ(["AI call"]) --> Lanes --> AI(["Ollama / Gemini / 26B"])
```

The five `MODEL_PROFILES` map onto these lanes:

| Profile            | Lane               | Cloud auto-route |
| ------------------ | ------------------ | ---------------- |
| `tutor_fast`       | `student_chat`     | Never            |
| `translation_fast` | `live_translation` | Gemma 26B        |
| `notes_balanced`   | `background`       | Gemma 26B        |
| `admin_balanced`   | `admin`            | Gemini           |
| `deep_optional`    | `background`       | Gemini + think   |

---

## Database Schema

11 tables in a single SQLite file. WAL mode. Foreign keys enforced on every connection.

```mermaid
erDiagram
    students ||--o{ sessions : ""
    students ||--o{ topic_mastery : ""
    students ||--o{ quiz_results : ""
    students ||--o{ class_notes : ""
    students ||--o{ import_history : ""
    sessions ||--o{ messages : ""
    sessions ||--o{ quiz_results : ""
    sessions ||--o{ class_notes : ""
    teachers ||--o{ teacher_sessions : ""
    teacher_quizzes ||--o{ quiz_results : ""

    students {
        TEXT id PK
        TEXT name
        TEXT language
        TEXT grade_level
        TEXT username
        TEXT password_hash
        TEXT pin_visible
    }
    sessions {
        TEXT id PK
        TEXT student_id FK
        INTEGER message_count
        TEXT topics_covered
    }
    messages {
        TEXT id PK
        TEXT session_id FK
        TEXT role
        TEXT content
        TEXT tool_name
    }
    topic_mastery {
        TEXT student_id PK
        TEXT topic PK
        REAL mastery
        TEXT level
    }
    quiz_results {
        TEXT id PK
        TEXT student_id FK
        TEXT quiz_id FK
        TEXT session_id FK
        TEXT topic
        REAL score
    }
    teachers {
        TEXT id PK
        TEXT username
        TEXT display_name
        TEXT email
        TEXT password_hash
        INTEGER is_admin
    }
    teacher_sessions {
        TEXT token PK
        TEXT teacher_id FK
        TEXT expires_at
    }
    teacher_quizzes {
        TEXT id PK
        TEXT title
        TEXT questions
        TEXT assigned_to
    }
    materials {
        TEXT id PK
        TEXT subject
        TEXT file_path
        INTEGER faiss_indexed
    }
    class_notes {
        TEXT id PK
        TEXT student_id FK
        TEXT session_id FK
        TEXT raw_transcript
        INTEGER faiss_indexed
    }
    live_games {
        TEXT id PK
        TEXT phase
        TEXT join_code
        TEXT questions_json
    }
```

The `topic_mastery` table is the adaptive engine. Every quiz result flows through `db.update_mastery()`, which recalculates the running average score and maps it to a level (`beginner → developing → proficient → advanced → mastered`). The next chat turn calls `db.get_student_mastery()` and injects this into the system prompt via `prompts.py`.

---

## Voice Pipeline

Two separate subsystems. Both run locally with no cloud dependency.

```mermaid
flowchart LR
    subgraph ASR["Speech → Text  (whisper_client.py)"]
        MIC["Browser\nMediaRecorder 16 kHz"] -->|audio blob| WC["whisper_client\n.transcribe_audio()"]
        WC --> BIN{bin/whisper\nexists?}
        BIN -->|Yes| WPP["whisper.cpp\nGGML model\nfast offline"]
        BIN -->|No| HF1["transformers\nopenai/whisper-tiny\nlocal fallback"]
        WPP & HF1 --> TXT(["text → body.message"])
    end

    subgraph TTS["Text → Speech  (tts_client.py)"]
        REQ["POST /api/tts/speak\ntext + language"] --> VM["VOICE_MAP\nlanguage lookup"]
        VM -->|en ar de es fr hi\npt ru sw tr zh zh-TW| PIP["bin/piper\n.onnx voice file\nThreadPoolExecutor"]
        VM -->|ta so bn| MMS1["MMS-TTS\nfacebook/mms-tts-*\nlazy cached"]
        VM -->|am ur| MMS2["MMS-TTS + uroman\ntransliteration"]
        PIP & MMS1 & MMS2 --> WAV(["WAV → browser audio"])
    end
```

`tts_client.py` uses a `ThreadPoolExecutor` (not `asyncio.subprocess`) because Piper spawns a subprocess and Windows' `ProactorEventLoop` cannot run asyncio subprocesses from an async context. Do not refactor this to `asyncio.create_subprocess_exec` without testing on Windows.

---

## Agent Tool System

The tutor model has access to four tools. They are only invoked when `_should_plan_tools()` returns `True` — a keyword check on the student's message. This avoids the latency of a planning generation on every chat turn.

```mermaid
flowchart TD
    MSG(["Student message"]) --> KW{"_should_plan_tools()\nquiz · mastery · curriculum\nprogress · detect language"}

    KW -->|No| DIRECT(["stream_chat() directly"])
    KW -->|Yes| PLAN["complete_with_tools()\nTOOL_SCHEMAS  12 s timeout"]

    PLAN --> TCE{ToolCallEvent?}
    TCE -->|No| DIRECT
    TCE -->|Yes| DISP["_dispatch_tool()"]

    DISP --> T1["detect_language\nlangdetect.detect_langs()"]
    DISP --> T2["retrieve_curriculum\nfaiss_store.query k=3"]
    DISP --> T3["generate_quiz\nmodel LLM + teacher_quizzes table\nteacher questions appear first"]
    DISP --> T4["update_progress\ndb.update_mastery()"]

    T1 & T2 & T3 & T4 --> FOLLOW["stream_chat()\ntool followup"]
    FOLLOW --> SSE(["SSE tokens → browser"])
```

---

## Startup Sequence

```mermaid
flowchart TD
    UV(["uvicorn app.main:app"]) --> LS["lifespan()"]

    LS --> A["load_user_network_settings()\nreads data/user_settings.json"]
    A --> C["_ensure_noto_fonts()\n10 script TTFs for PDF export\nskipped if already present"]
    C --> D["_ensure_ssl_cert()\nself-signed cert covering all LAN IPs\nskipped if already present"]
    D --> E["db.init_db()\nCREATE TABLE IF NOT EXISTS × 11"]
    E --> F["start_captive_portal(ip)\nDNS :53  needs root/admin"]
    F --> G["start_mdns(port=8000)\n_optilearn._tcp"]
    G --> H["asyncio.create_task\nwarmup in background\ntutor 120s · translation 60s · embeddings 120s"]
    G --> I["db.purge_expired_sessions()\nstale JWTs"]
    I --> J["asyncio.create_task\n_session_cleanup_loop()\nevery 3600 s"]
    J --> READY(["Server ready"])
```

Warmup runs as a background task so the server accepts requests immediately. Students who connect within the first ~10 seconds may see a slower first response while Ollama loads the model weights.

---

## Frontend Structure

```mermaid
graph TD
    MAIN["main.jsx"] --> QCP["QueryClientProvider\nreact-query"]
    QCP --> AUTH["AuthProvider\nAuthContext — localStorage"]
    AUTH --> BR["BrowserRouter"]
    BR --> COR["CanonicalOriginRedirect\nredirects to hotspot server IP"]
    BR --> HB["NetworkHeartbeat\nPOST /api/network/heartbeat every 10 s\nstudent sessions only"]
    BR --> NG["NetworkGate\npolls /api/health every 5 s\nOfflineScreen after 2 failures"]
    NG --> ROUTES["Routes"]

    subgraph Public["No auth"]
        ROUTES --> HOME["/ HomeScreen"]
        ROUTES --> SETUP["/setup SetupScreen"]
        ROUTES --> JN["/join LiveQuizCodeEntry"]
        ROUTES --> QJOIN["/live-quiz/join/:id LiveQuizJoin"]
        ROUTES --> QPLAY["/live-quiz/play/:id LiveQuizPlay"]
    end

    subgraph TGuard["TeacherRoute guard\nvalidates JWT via GET /api/auth/teacher/me"]
        ROUTES --> TD["/teacher TeacherDashboard"]
        ROUTES --> TSP["/teacher/student/:id StudentProgress"]
        ROUTES --> TQUIZ["/teacher/live-quiz/:id LiveQuizHost"]
    end

    subgraph SGuard["StudentRoute guard\nchecks AuthContext.studentId"]
        ROUTES --> SL["StudentLayout sidebar\n/student/:id/*"]
        SL --> SH["index StudentHome"]
        SL --> SS["/session StudentSession"]
        SL --> SLT["/live-translator LiveTranslator"]
        SL --> STL["/translate-learn TranslateLearn"]
        SL --> LMS["courses · assignments\ngrades · calendar\nannouncements · course/:subject"]
    end
```

---

## Cross-Cutting Concerns

**Error handling in SSE streams**  
Once a `StreamingResponse` has started, HTTP status codes are no longer available. Errors are sent as `data: {"type": "error", "message": "..."}` events. The browser-side `useSSE` hook listens for this event type. Never throw an unhandled exception inside an `event_stream()` generator — the stream closes silently.

**Multilinguality throughout**  
`ensure_ascii=False` is used in every `json.dumps` call that produces SSE output. Arabic, Amharic, Tamil, and Devanagari script would otherwise produce `\uXXXX` escapes that inflate payload size 3–6×. This matters on 2G / shared hotspot bandwidth. The check is centralised in `app/api/sse.py`.

**Network mode is runtime-configurable**  
`USE_LOCAL_OLLAMA=true` in `.env` sets the default. The teacher can toggle between `offline / auto / online` at runtime via `POST /api/settings/network-mode` without restarting the server. The setting persists to `data/user_settings.json`. Read `model_client.load_user_network_settings()` to understand the precedence logic.

**API keys can be updated live**  
`POST /api/settings/api-keys` writes new values to the active `.env` file and applies them immediately to the `settings` singleton — no server restart needed. In desktop mode the active `.env` is in the OS user data directory (pointed to by `ENV_FILE`); in web mode it is `optilearn/.env`.

**HTTPS is required for microphone access**  
Browser `MediaRecorder` API requires a secure context. The server auto-generates a self-signed certificate on first startup (`_ensure_ssl_cert()`) covering all detected LAN IPs. Students must accept the certificate warning once. Voice features do not work over plain HTTP even on the local network.

**Captive portal requires elevated privileges**  
`start_captive_portal()` binds to UDP port 53. On Linux/macOS this requires root. On Windows it requires Administrator. If the server starts without elevated privileges the captive portal is silently skipped and students must navigate to the server URL manually (or scan the QR code from the teacher dashboard).

---

## Where to Start

| Task                                      | Start here                                                                                 |
| ----------------------------------------- | ------------------------------------------------------------------------------------------ |
| Add a new student-facing feature          | `app/api/routes/chat.py` — understand the SSE loop first                                   |
| Add a new teacher dashboard panel         | `app/api/routes/teacher.py` + `screens/TeacherDashboard.jsx`                               |
| Change how AI model is selected           | `app/services/model_client.py` — `MODEL_PROFILES` dict                                     |
| Change how mastery is calculated          | `app/services/db.py` — `update_mastery()`                                                  |
| Add a new language to TTS                 | `app/services/tts_client.py` — `VOICE_MAP` dict                                            |
| Add a database column                     | `app/services/db.py` — `_SCHEMA_SQL` (use `ALTER TABLE` migration)                         |
| Change the student system prompt          | `app/core/prompts.py`                                                                      |
| Add a new agent tool                      | `app/tools/agent_tools.py` — add to `TOOL_SCHEMAS` and implement function                  |
| Understand the offline / online switching | `app/services/model_client.py` — `load_user_network_settings()` and `check_connectivity()` |
| Debug a slow response                     | `GET /api/health` → `scheduler.lanes` — check which lane is saturated                      |
| Change the desktop first-launch wizard    | `desktop/electron/setup.html` + `desktop/electron/main.js` (IPC handlers)                  |
| Change how ENV_FILE is resolved           | `app/core/config.py` — `_env_file` at module top                                           |
| Reset a teacher password or wipe the DB   | `optilearn/scripts/reset-admin.sh`                                                         |
| Update API keys without restarting        | `POST /api/settings/api-keys` or `desktop/electron/setup.html` step 1                      |
