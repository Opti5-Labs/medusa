"""
Project architecture generation: static, read-only analysis of a scanned
repository's tree that produces a bounded component graph and a Mermaid
diagram. See app/pipelines/architecture.py for the orchestrator.

Nothing in this package executes repository code, installs dependencies, or
imports repository modules. Source files are read as text and parsed with
Python's stdlib `ast` (Python) or bounded regexes (JS/TS/JSX/TSX); manifests
are read with `json.loads` / `tomllib.loads` / narrow line-anchored regexes
only — never a YAML loader, never `eval`/`exec`/`subprocess`/`importlib`.
"""
