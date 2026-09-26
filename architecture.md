# Medusa Architecture

> Medusa is an evidence-driven, multi-agent debugging system. It investigates a defect from several
> angles, reproduces the failure, tests competing patches in isolated sandboxes, and recommends a fix
> based on execution evidence rather than model confidence.

This document describes the target design. Hard rules, limits and contracts live in `CLAUDE.md`;
the plan and team split live in `PROJECT.md`. If they disagree, `CLAUDE.md` wins.

## Core principle

```text
LLMs (Bob, Granite)      → reason about the problem: investigate, diagnose, propose patches, explain
Deterministic Python     → prove whether a patch works: run, verify, rank
```

A patch is never "working" because a model says so. On the OptiLearn path, the sandbox decides.
On general repos nothing is executed, so nothing is ever labelled reproduced or tested.

## System overview

```text
┌────────────────────────────────────────────────────────────┐
│             Next.js frontend (App Router, TS)              │
│  Landing → Issues → Reproduce panel → Debug race → Result  │
└─────────────────────────────┬──────────────────────────────┘
                              │ REST + SSE
┌─────────────────────────────┴──────────────────────────────┐
│                      FastAPI backend                       │
│   ingest · scan · repro · debug · verify · recommend · zip │
└──────┬───────────────┬────────────────┬───────────────┬────┘
       │               │                │               │
  ┌────┴────┐   ┌──────┴─────┐   ┌──────┴─────┐  ┌──────┴──────┐
  │ Granite │   │ GitHub API │   │ Bob        │  │ Docker      │
  │ watsonx │   │ Issues,    │   │ golden run │  │ sandbox     │
  │         │   │ read-only  │   │ replay     │  │ OptiLearn   │
  └─────────┘   └────────────┘   └────────────┘  └─────────────┘
   general repos (read only)       OptiLearn demo (executes)
```

FastAPI is the only thing the frontend talks to. Agents, the sandbox and external APIs are called by
the backend pipelines, never directly by the browser.

## Two paths

| | OptiLearn demo (verified) | General repos (analysis only) |
|---|---|---|
| Input | Built-in demo repo with a known, real bug | Public GitHub URL or zip |
| Executes code? | Yes, only inside the sandbox | **Never** |
| Investigation | Recorded Bob session (replay) | Granite |
| Reproduce result | `reproduced` / `not_reproducible` | `plausible` + `confidence` |
| Fix result | `passed` / `failed` per candidate | `not_applicable` |
| UI label | "Recorded Bob session" for Bob output; sandbox results are live | "Analysis only. Code was not executed. Patches are not verified." |

## Pipeline (OptiLearn path)

```text
Issue
  │
  ▼
Sandbox: run the reproducer against the original code
  │
  ▼
BUG GATE ── original code fails? ── no ──► not_reproducible, with reasoning shown. Stop.
  │ yes
  ▼
Investigators (recorded Bob session if present, else Granite live), given the
runtime evidence: the failing test, its source and its assertion output
  ├── Runtime investigator      what fails, with which input
  ├── Repository investigator   traces that input line by line to the bad return
  └── Skeptic                   rejects claims about code the failing input never reaches
  │
  ▼
Root-cause synthesis            must explain the runtime evidence; never edits code
  │
  ▼
reproduced
  │
  ▼
Candidate fixes (2–6, default 4), each a different strategy, from Granite,
  given the acceptance criteria a developer would get (failing test + checks that
  must keep passing)
  (full replacement of the target function, spliced in by Medusa); prepared
  candidates fill any slot Granite cannot, labelled as prepared
  │
  ▼
Debug race: one sandbox per candidate, in parallel
  apply patch → run reproducer → run test suite
  │
  ▼
One revision round: a failing Granite candidate gets its failing checks and
  assertion output back, revises once, and is re-tested (labelled in the UI)
  │
  ▼
Verification (deterministic)
  │
  ▼
Recommendation (deterministic ranking + recorded Bob explanation)
  │
  ▼
Zip download with the chosen fix applied
```

### Reproducer

The OptiLearn reproducer is a hand-written failing test baked into the sandbox image. It is not
generated at runtime; a live-generated reproducer is the easiest way for the demo to fail.

### Bug gate

Candidate fixes only run if the original code fails the reproducer in the sandbox. Otherwise the
result is `not_reproducible`, which is a valid outcome, not an error to hide.

### Debug race

- Each candidate gets its own container with the limits in `CLAUDE.md` (no network, 512 MB,
  0.5 CPU, read-only root, no credentials, 90 s wall clock, always removed).
- Candidates run concurrently under the global `MAX_CONCURRENT_SANDBOXES` semaphore.
- A candidate whose patch doesn't apply or whose run fails shows `failed` in its own panel.
  It never stops the other candidates.
- The test-suite baseline on the original code runs **once per session**, not once per candidate.

### Verification (no LLM involved)

For each candidate:

| Check | Source |
|---|---|
| Reproducer fixed | original fails, patched passes |
| Tests passed / total | test run in the candidate sandbox |
| Regression | a test that passed in the baseline now fails |
| Patch size | files changed, lines added, lines removed (from the diff) |

Results go in `FixAttempt.sandbox_status` and `FixAttempt.test_results`.

### Recommendation

1. Eligible: reproducer fixed, no regression, all tests pass.
2. Rank eligible candidates by tests passed, then smallest patch.
3. The reason is generated from the metrics themselves (checks passed, regressions, patch
   size), so it can never disagree with what ran live.

The user can pick any other passing candidate for download.

## Pipeline (general repos)

```text
Ingest (limits, safe extract) → file selection → Granite scan → issues (+ GitHub Issues)
  │
  ▼
Reproduce: one Granite call → root-cause hypothesis with file/line citations → plausible + confidence
  │
  ▼
Debug: one Granite call per candidate (default 2) → proposed patch → not_applicable
```

Kept to one or two Granite calls per step so it fits the timeouts (30 s per call, one retry).
There is no runtime investigator here because there is no runtime data.

## Models

| Model | Used for | Where |
|---|---|---|
| IBM Bob | OptiLearn investigators and synthesis | `agents/bob.py`. `BOB_MODE=replay` on the server (streams `golden/optilearn/investigation.jsonl` when present); `live` for local capture only |
| Granite on watsonx.ai | Scan, general-repo reasoning, patch proposals, OptiLearn fix candidates, and the OptiLearn investigators until a Bob run is recorded | `agents/granite.py` (REST), `agents/investigators.py`, `agents/fixers.py`. JSON-only prompts validated with Pydantic |

Bob credentials never live on the deployed server.

Model calls send only the code the bug lives in (the module up to the target function and
the settings it reads), and identical prompts are answered from a 6-hour in-process cache
(temperature 0, so the answer would be the same). Both keep the watsonx.ai token quota
from running out mid-demo. When the quota is used up, every Granite step says so and the
demo continues on the sandbox result and the prepared candidates.

## Streaming

Every long operation streams `LogEvent`s over SSE (`sse-starlette`), then a `done` event.
Agent and candidate identity is carried in `LogEvent.source`:

```text
investigator:runtime · investigator:repository · investigator:skeptic
synthesis · sandbox · candidate:c1 … candidate:c6 · granite · recommendation
```

No new event format. `contracts.py` and `frontend/lib/api.ts` stay the single shape definition.

## Session state

In memory only, deleted when the run ends or after the 30-minute TTL. No database.
Each repro or debug run tracks a simple status:

```text
INVESTIGATING → REPRODUCING → REPRODUCED | NOT_REPRODUCIBLE
  → TESTING_FIXES → RECOMMENDED → COMPLETED
(any step) → FAILED, always with a visible error event
```

## Code layout

New work slots into the existing layout. Nothing already built moves.

| Concept | Location |
|---|---|
| Ingest, limits, safe extraction | `backend/app/ingest/` (built) |
| Scan pipeline | `backend/app/pipelines/scan.py` (built, Granite pending) |
| Reproduce orchestration and bug gate | `backend/app/pipelines/repro.py` |
| Debug race | `backend/app/pipelines/debug.py` |
| Verification and ranking | `backend/app/pipelines/verify.py` |
| General-repo file selection | `backend/app/pipelines/context.py` |
| Bob replay | `backend/app/agents/bob.py`, `golden/optilearn/` |
| Granite investigators and fixers | `backend/app/agents/investigators.py`, `fixers.py` |
| Granite client | `backend/app/agents/granite.py` |
| Sandbox runner (only execution entry point) | `backend/app/sandbox/runner.py` |
| OptiLearn image and reproducer | `sandbox/optilearn/` |
| Reproduce and debug panels | `frontend/app/investigate/[id]/`, `frontend/app/components/` |
| nginx and systemd | `deploy/` |

## Deployment

One AWS EC2 instance. nginx serves the Next.js frontend and proxies `/api` to FastAPI on
`127.0.0.1:8000`, with buffering off for SSE. FastAPI launches sandbox containers on the same host
from the pre-built image. Secrets live in `/etc/Medusa/env`.

```text
Internet → nginx ─┬─► Next.js frontend
                  └─► /api → FastAPI ─┬─► watsonx.ai (Granite)
                                      ├─► GitHub API (read only)
                                      ├─► golden run files (Bob replay)
                                      └─► Docker sandbox (OptiLearn only)
```
