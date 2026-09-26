# Medusa

> Find the bug, prove it, fix it, and show your work.

IBM Bob 2.0 Hackathon entry (lablab.ai, 25–27 Sep 2026).
Submission deadline: **Sun 27 Sep 2026, 15:00 UTC (20:30 Colombo)**. Target: submit by 14:00 Colombo.

## Problem

When a bug report lands, a developer spends most of the time before the fix on three things:
reading unfamiliar code to find where the issue lives, building a reliable way to reproduce it,
and trying fixes one at a time until one passes. That loop is slow, manual and easy to get wrong,
especially for small teams and research projects with no dedicated QA.

## Solution

Medusa turns that loop into one guided flow:

1. **Scan** a repo (demo, public GitHub URL, or zip) and list issues from static review and GitHub Issues, each tagged Low / Medium / High.
2. **Reproduce** an issue. On the demo path this runs in a locked-down sandbox and streams a live log to a clear verdict. On other repos it produces an evidence-backed analysis, labelled as not executed.
3. **Debug** by testing 2–6 candidate fixes in parallel, each in its own sandbox, ranked by test pass rate and patch simplicity. Download the fixed code as a zip.

IBM Bob drives the investigation and the final recommendation. Granite on watsonx.ai handles bulk scanning and reasoning on arbitrary repos.

## User flow

1. Landing page: **Run the demo**, **Link a GitHub repo**, or **Upload a zip**. Size and file-count limits are checked immediately, with a plain message if exceeded.
2. Issue list: one row per issue with priority, short description, source (scan or GitHub Issue), and three actions: Reproduce, Debug, Reproduce and Debug.
3. Reproduce panel: live log ending in _reproduced_ with a root-cause account, or _not reproducible_ with the reasoning shown. General repos end in _plausible_ with a confidence score.
4. Debug panels: side-by-side candidates with live logs, toggleable down to two. Ends with a named recommendation; the user can pick any other passing candidate.
5. Output: zip download with the chosen fix applied.

## Architecture

```
                    ┌──────────────────────┐
                    │   React frontend     │
                    └──────────┬───────────┘
                               │ REST + SSE
┌──────────────────────────────┴──────────────────────────────┐
│                       FastAPI backend                        │
│      ingest · scan · reproduce · debug · recommend · zip     │
└───────┬──────────────┬──────────────────┬──────────────┬─────┘
        │              │                  │              │
   ┌────┴────┐   ┌─────┴─────┐      ┌─────┴─────┐  ┌─────┴──────┐
   │ Granite │   │ GitHub API│      │ Bob agents│  │  Docker    │
   │watsonx.ai│  │ Issues,   │      │ golden run│  │  sandbox   │
   │         │   │ read-only │      │ replay    │  │ OptiLearn  │
   └─────────┘   └───────────┘      └───────────┘  └────────────┘
    general repos (read-only)        OptiLearn demo (executes)
```

| Component               | Role                                                                                                          |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| Frontend (React + Vite) | Landing page, issue list, live reproduce and debug panels                                                     |
| Backend (FastAPI)       | Orchestrates every pipeline, REST + SSE endpoints, limits and rate limiting                                   |
| Agent layer             | Bob for OptiLearn investigators and the final recommendation; Granite for scanning and general-repo reasoning |
| Execution layer         | Ephemeral Docker containers, OptiLearn only, no network, hard CPU / memory / time limits, no credentials      |
| GitHub integration      | Read-only Issues for any linked public repo                                                                   |

Everything runs on one AWS EC2 instance: nginx serves the frontend and proxies `/api` to FastAPI, which launches sandbox containers on the same host. Nothing persists between sessions.

### Key design decisions

- **One execution path.** Only the OptiLearn demo runs code. General repos are analysed, never executed. One sandbox to harden and rehearse, and no untrusted builds on a public URL.
- **Bob live on the deployed site** (changed from the original replay plan). Bob Shell runs headless on the server as an independent investigator, read-only and capped at `BOB_MAX_COST` (0.25 Bobcoins) and `BOB_MAX_TURNS` (6) per run, with a 6-hour answer cache and per-IP rate limits. `BOB_MODE=replay` can still stream a recorded session from `golden/optilearn/investigation.jsonl`, but only a real captured run may go there.
- **Honest outputs.** Reasoning-only results are never presented as reproduced or tested. A clean "could not reproduce" is a valid result, not a failure to hide.
- **Stateless by design.** No database, no accounts, no GitHub write access.

## Scope

### Core (must work for submission)

- OptiLearn demo path end to end: investigate, reproduce, debug, recommend, inside the sandbox
- Landing page with all three entry points
- Read-only scan of a real GitHub repo or uploaded zip, with timeouts and rate limits
- GitHub Issues merged into the issue list
- Reasoning-only reproduce and debug for general repos, with real file and line citations
- Zip download of the OptiLearn fix

### Cut (not attempted)

- Sandboxed execution on general repositories
- GitHub OAuth and pull-request output
- Persistence, accounts, multi-user state

### Stretch (only after core is frozen and rehearsed, in this order)

1. Candidate count toggle (2–6) polish on the OptiLearn path
2. A second OptiLearn demo bug as a fallback
3. Sandboxed execution on general Python repos (Person 1 only, only with hours to spare)

## Team

| Person                 | Owns                                                                                                                                  |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| 1 — Engine             | Bob Shell spike, OptiLearn harness, sandbox image and runner, investigator prompts, capturing and rehearsing the golden run           |
| 2 — Backend            | FastAPI endpoints and SSE, ingest and limits, scan pipeline on Granite, GitHub Issues merge, reasoning-only pipelines, data contracts |
| 3 — Frontend and proof | React app, AWS deployment, end-to-end testing, `bob_sessions` screenshots, demo video, slides, submission text                        |

## Timeline

H0 is when the team starts. If H0 is Fri 23:00 Colombo, feature freeze (H20) is Sat 19:00 and the submission target is Sun 14:00.

| Hours  | Person 1 (Engine)                                                            | Person 2 (Backend)                                                      | Person 3 (Frontend and proof)                                |
| ------ | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------ |
| H0–2   | Agree contracts; Bob Shell spike; confirm the OptiLearn bug still reproduces | Repo scaffold, FastAPI skeleton, contracts in code, watsonx key working | Vite scaffold, types from contracts, AWS instance up         |
| H2–8   | Investigators reliably find the golden bug; capture the golden run           | Ingest with limits; scan pipeline on Granite; GitHub Issues             | Landing page and issue list on mock data                     |
| H8–14  | Sandbox runner; OptiLearn repro and parallel debug end to end                | Reasoning repro and debug; recommendation; zip download                 | SSE log streams; side-by-side debug panels; wire to real API |
| H14–18 | Rehearse the golden run on EC2 until it is boring                            | Integration fixes; rate limits; error messages                          | Deploy; full end-to-end smoke test with the team             |
| H18–20 | On call for integration bugs                                                 | Edge cases: oversize repos, bad zips, timeouts                          | Collect every teammate's `bob_sessions` screenshots          |
| H20    | **Feature freeze**                                                           | **Feature freeze**                                                      | **Feature freeze**                                           |
| H20–24 | Demo rehearsal                                                               | README, `.env.example`, secret scan                                     | Demo video, slides, submission text                          |

After H24: sleep in shifts, re-rehearse the demo cold, fix what the smoke test found, submit early.

## Bob IDE plan (evidence for judging)

Bob IDE must be a core, visible part of how Medusa was built. Every Bob task below gets a session-summary screenshot in `bob_sessions/`, taken as soon as the task finishes.

| #   | Task in Bob IDE                                                  | Owner    | Mode            |
| --- | ---------------------------------------------------------------- | -------- | --------------- |
| 1   | `/init` to generate `AGENTS.md` for the repo                     | Person 2 | Agent           |
| 2   | Design and implement the sandbox runner with the security limits | Person 1 | Plan, then Code |
| 3   | Write the three investigator prompts and the synthesis step      | Person 1 | Code            |
| 4   | Implement the scan pipeline and chunking                         | Person 2 | Plan, then Code |
| 5   | Generate tests for ingest limits and zip-slip                    | Person 2 | Code            |
| 6   | Build the debug panels and SSE log component                     | Person 3 | Code            |
| 7   | Security review of the sandbox and ingest code before freeze     | Person 1 | Code review     |
| 8   | Commit messages and PR descriptions for the final merges         | Everyone | Commit / PR     |

Check Bobcoin usage (Settings, General) after task 1 and adjust. Spread tasks across all three accounts so no one runs dry.

Screenshot naming: `Medusa_task01_init_agents.png`, `Medusa_task02_sandbox_runner.png`, and so on.

## Demo script (about 3 minutes)

1. **Problem (20 s).** A real bug in OptiLearn that took a developer hours to track down by hand.
2. **Scan (30 s).** Run the demo. Issues appear with priorities and sources.
3. **Reproduce (45 s).** Bob's three investigators stream their reasoning; the sandbox confirms the bug with a live log.
4. **Debug (45 s).** Four candidate fixes run in parallel; two pass, one is recommended with a reason. Download the zip.
5. **Any repo (30 s).** Paste a public GitHub URL. Scan and a reasoning-only analysis with real citations, clearly labelled as not executed.
6. **Impact (10 s).** Time from issue to verified fix, manual versus Medusa. Measure this during rehearsal; never estimate it.

## Risks and fallbacks

| Risk                                               | Fallback                                                                                           |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Bobcoins run out                                   | Per-run cost/turn caps, answer cache and rate limits; set `BOB_MODE=replay` with a real captured run, or `off` (Granite and prepared candidates carry on) |
| Granite slow or rate-limited                       | Cap chunks; cache the demo repo's scan result                                                      |
| Sandbox flaky on EC2                               | Rehearse on EC2 by H16; keep a recorded run for the video                                          |
| GitHub API rate limit                              | `GITHUB_TOKEN` (fine-grained, no scopes)                                                           |
| Credential leak                                    | `.env` gitignored from commit one; run a secret scanner before every push to main                  |
| Questions about OptiLearn pre-dating the hackathon | Confirm on Discord early; README states what existed before 25 Sep and what was built this weekend |

## Submission checklist

- [ ] Public repo with `bob_sessions/` containing a PNG summary for every Bob task
- [ ] No IBM, watsonx, GitHub or AWS credentials anywhere in the repo or its history
- [ ] Working prototype online (AWS)
- [ ] Demo video
- [ ] Slide deck
- [ ] README: what it does, how to run it, what pre-existed, list of any public data sources used
- [ ] Granite model is not one of the three banned watsonx models
- [ ] Golden run rehearsed on the deployed instance within the last few hours
- [ ] Submitted well before 20:30 Colombo, Sunday
