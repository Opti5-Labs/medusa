# Curated OptiLearn architecture

This directory is a versioned, hand-authored artifact, not pipeline output.
It is what `POST /api/scans/{scan_id}/architecture` returns for the demo path
(`repo_source == "demo"`), deterministically and without calling Granite or
Bob. See `app/architecture/curated.py` for the loader and
`app/pipelines/architecture.py::_run_curated` for how it's wired in.

## Files

- `architecture.json` — the source of truth: 12 architecture-level
  components and 15 relationships, plus technology stack, entrypoints,
  external services, data stores and deployment. `app/architecture/mermaid.py`
  generates the overview diagram (`ArchitectureReport.mermaid`) from this file
  at load time — it is never hand-written, so it can't drift from the JSON.
- `detail.mmd` — the full 74-node diagram, extracted verbatim from
  `optilearn-architecture.md` at the repo root. Served as
  `ArchitectureReport.detail_mermaid` for a "show full detail" view. This is
  the *only* hand-written Mermaid in the system; it is still run through
  `mermaid.validate()` on load like any other diagram.
- `manifest.json` — version string, upstream provenance, the two evidence
  roots (see below), expected component/relationship counts, and a sha256 of
  each of the two files above.

## Provenance

Both source documents (`optilearn-architecture.md`,
`optilearn-architecture-detailed.md`, repo root) describe the **upstream**
OptiLearn project (github.com/Ilakiancs/OptiLearn) in full — Electron shell,
19-ish FastAPI routers, SQLite, FAISS, Ollama, the works. Medusa's sandboxed
demo (`sandbox/optilearn/src/`) bundles only 7 real modules from it:
`app/core/config.py`, `app/core/grades.py`, and five files under
`app/services/` (`db.py`, `generated_cache.py`, `model_scheduler.py`,
`telemetry.py`, `whisper_client.py` — the last one is where the demo's
reproducible bug lives, in `_resolve_hf_asr_model`).

`architecture.json` reflects that split with **two-tier evidence**:

- Evidence with `"verified": true` resolves under `sandbox/optilearn/src/`
  and is checked against the filesystem on every load (`curated.py`). There
  are exactly 7 such references, one per bundled module.
- Evidence with `"verified": false` carries a `"url"` into the upstream
  repository instead and is never filesystem-checked. The 5 components with
  no bundled evidence (desktop shell, network layer, React frontend, the
  FastAPI server/routing layer, agent tools) are entirely upstream-only.

## Updating this artifact

If you edit `architecture.json` or `detail.mmd`:

1. Update `expected_components` / `expected_relationships` in
   `manifest.json` if the counts changed.
2. Recompute both hashes and update `manifest.json`:
   ```bash
   cd backend
   .venv/Scripts/python.exe -c "
   import hashlib
   for f in ('app/architecture/curated/optilearn/architecture.json',
             'app/architecture/curated/optilearn/detail.mmd'):
       print(f, hashlib.sha256(open(f, 'rb').read()).hexdigest())
   "
   ```
3. Run `pytest tests/test_arch_curated.py` — it re-validates every
   `verified: true` path against `sandbox/optilearn/src/`, both diagrams
   against `mermaid.validate()`, and the counts against `manifest.json`.

A stale hash or a wrong count is a deliberate hard failure (502 at runtime,
a failing test locally) — this artifact is meant to break loudly, not drift
silently.

## Why curated, not generated

The upstream repository is not checked out anywhere Medusa can read at
runtime, and regenerating this diagram from the ~12 files actually bundled in
`sandbox/optilearn/src/` would produce a far smaller, far less informative
graph than the one a human familiar with the real project can draw. Treating
it as a curated asset (like `app/demo/optilearn_scan.json`) keeps the demo
deterministic, model-free, and reviewable, while still being validated like
any other architecture report.
