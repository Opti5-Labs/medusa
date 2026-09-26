"""
Deterministic manifest and deployment-file inspection.

Every parser here reads declarative data only:
    - package.json / pyproject.toml  -> json.loads / tomllib.loads
    - requirements*.txt              -> line splitting
    - Dockerfile*                    -> line-anchored regex on known directives
    - docker-compose*.y*ml, CI yaml  -> a narrow regex for top-level key NAMES
                                        only (no YAML loader — see note below)
    - .env.example / .env.sample     -> variable NAMES only, never values;
                                        real .env files are never opened
                                        (see app.architecture.inventory.is_secret_file)

No YAML library is used anywhere in this module. A real YAML loader
(`yaml.load`/`safe_load`) can execute Python objects via `!!python/object`
tags or be used for a "billion laughs" expansion bomb; compose/CI files are
untrusted repository input, so they get a hand-rolled top-level-key scanner
instead, which cannot do either.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

from app.architecture.inventory import ENV_EXAMPLE_NAMES, Inventory

_MAX_MANIFEST_BYTES = 400_000

# ── Framework / tooling classification tables ────────────────────────────────
# Keyed by lowercase dependency name as it appears in package.json / pyproject
# / requirements.txt. Kept as flat dicts so extending them is a one-line change.

_FRAMEWORKS: dict[str, str] = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "starlette": "Starlette",
    "celery": "Celery",
    "pydantic": "Pydantic",
    "sqlalchemy": "SQLAlchemy",
    "next": "Next.js",
    "react": "React",
    "react-dom": "React",
    "vue": "Vue",
    "svelte": "Svelte",
    "express": "Express",
    "@nestjs/core": "NestJS",
    "prisma": "Prisma",
    "@prisma/client": "Prisma",
    "tailwindcss": "Tailwind CSS",
    "alembic": "Alembic",
    "redis": "Redis client",
    "boto3": "AWS SDK (boto3)",
    "uvicorn": "Uvicorn",
    "gunicorn": "Gunicorn",
}
_BUILD_SYSTEMS: dict[str, str] = {
    "vite": "Vite",
    "webpack": "Webpack",
    "rollup": "Rollup",
    "turbo": "Turborepo",
    "esbuild": "esbuild",
    "setuptools": "setuptools",
    "hatchling": "Hatch",
    "poetry-core": "Poetry",
}
_TEST_FRAMEWORKS: dict[str, str] = {
    "pytest": "pytest",
    "jest": "Jest",
    "vitest": "Vitest",
    "playwright": "Playwright",
    "mocha": "Mocha",
    "cypress": "Cypress",
}

_DOCKERFILE_RE = re.compile(
    r"(^|/)Dockerfile(\.[A-Za-z0-9_.-]+)?$|\.dockerfile$", re.IGNORECASE
)
_COMPOSE_RE = re.compile(
    r"(^|/)(docker-)?compose(\.[A-Za-z0-9_-]+)?\.ya?ml$", re.IGNORECASE
)


@dataclass
class ManifestFacts:
    frameworks: set[str] = field(default_factory=set)
    build_systems: set[str] = field(default_factory=set)
    package_managers: set[str] = field(default_factory=set)
    test_frameworks: set[str] = field(default_factory=set)
    dependencies: set[str] = field(default_factory=set)  # raw lowercase names
    package_roots: set[str] = field(default_factory=set)  # rel dirs, "" = repo root
    workspaces: list[str] = field(default_factory=list)
    # Each: {"path", "kind", "detail"}
    entrypoints: list[dict] = field(default_factory=list)
    # Each: {"kind", "path", "detail", "services"}
    deployment: list[dict] = field(default_factory=list)
    env_var_names: list[str] = field(default_factory=list)


def _read(path: Path, max_bytes: int = _MAX_MANIFEST_BYTES) -> str | None:
    try:
        data = path.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


def _classify_dep(name: str, facts: ManifestFacts) -> None:
    key = name.strip().lower()
    if not key:
        return
    facts.dependencies.add(key)
    if key in _TEST_FRAMEWORKS:
        facts.test_frameworks.add(_TEST_FRAMEWORKS[key])
    elif key in _BUILD_SYSTEMS:
        facts.build_systems.add(_BUILD_SYSTEMS[key])
    elif key in _FRAMEWORKS:
        facts.frameworks.add(_FRAMEWORKS[key])


_REQ_SPLIT_RE = re.compile(r"[=<>~!;\[\s]")


def _parse_package_json(path: Path, rel_dir: str, facts: ManifestFacts) -> None:
    text = _read(path)
    if not text:
        return
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    facts.package_roots.add(rel_dir)
    facts.package_managers.add("npm")
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        deps = data.get(section)
        if isinstance(deps, dict):
            for name in deps:
                if isinstance(name, str):
                    _classify_dep(name, facts)
    workspaces = data.get("workspaces")
    if isinstance(workspaces, list):
        facts.workspaces.extend(w for w in workspaces if isinstance(w, str))
    elif isinstance(workspaces, dict) and isinstance(workspaces.get("packages"), list):
        facts.workspaces.extend(w for w in workspaces["packages"] if isinstance(w, str))
    main = data.get("main") or data.get("module")
    if isinstance(main, str):
        facts.entrypoints.append(
            {
                "path": f"{rel_dir}/{main}".lstrip("/"),
                "kind": "script",
                "detail": "package.json main entry",
            }
        )
    bin_field = data.get("bin")
    if isinstance(bin_field, str):
        facts.entrypoints.append(
            {
                "path": f"{rel_dir}/{bin_field}".lstrip("/"),
                "kind": "cli",
                "detail": "package.json bin entry",
            }
        )
    elif isinstance(bin_field, dict):
        for bpath in bin_field.values():
            if isinstance(bpath, str):
                facts.entrypoints.append(
                    {
                        "path": f"{rel_dir}/{bpath}".lstrip("/"),
                        "kind": "cli",
                        "detail": "package.json bin entry",
                    }
                )


def _parse_pyproject(path: Path, rel_dir: str, facts: ManifestFacts) -> None:
    try:
        data = tomllib.loads(path.read_text("utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return
    facts.package_roots.add(rel_dir)
    facts.package_managers.add("pip")
    project = data.get("project", {}) if isinstance(data, dict) else {}
    deps = project.get("dependencies") if isinstance(project, dict) else None
    if isinstance(deps, list):
        for dep in deps:
            if isinstance(dep, str):
                _classify_dep(_REQ_SPLIT_RE.split(dep, 1)[0], facts)
    scripts = project.get("scripts") if isinstance(project, dict) else None
    if isinstance(scripts, dict):
        for name in scripts:
            facts.entrypoints.append(
                {
                    "path": f"{rel_dir}".rstrip("/") or ".",
                    "kind": "cli",
                    "detail": f"pyproject console script '{name}'",
                }
            )
    build_backend = (
        data.get("build-system", {}).get("build-backend")
        if isinstance(data, dict)
        else None
    )
    if isinstance(build_backend, str):
        if "poetry" in build_backend:
            facts.build_systems.add("Poetry")
            facts.package_managers.add("Poetry")
        elif "hatchling" in build_backend:
            facts.build_systems.add("Hatch")
        elif "setuptools" in build_backend:
            facts.build_systems.add("setuptools")
    poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data, dict) else {}
    if isinstance(poetry, dict):
        for section in ("dependencies", "dev-dependencies"):
            deps = poetry.get(section)
            if isinstance(deps, dict):
                for name in deps:
                    if name.lower() != "python":
                        _classify_dep(name, facts)


def _parse_requirements(path: Path, facts: ManifestFacts) -> None:
    text = _read(path)
    if not text:
        return
    facts.package_managers.add("pip")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name = _REQ_SPLIT_RE.split(line, 1)[0].strip()
        if name:
            _classify_dep(name, facts)


_DOCKER_CMD_RE = re.compile(
    r"^\s*(FROM|EXPOSE|CMD|ENTRYPOINT|WORKDIR)\s+(.+?)\s*$", re.IGNORECASE
)


def _parse_dockerfile(path: Path, rel_path: str, facts: ManifestFacts) -> None:
    text = _read(path)
    if not text:
        return
    base_image = None
    cmd = None
    for line in text.splitlines():
        m = _DOCKER_CMD_RE.match(line)
        if not m:
            continue
        directive, value = m.group(1).upper(), m.group(2)
        if directive == "FROM" and base_image is None:
            base_image = value.split()[0]
        elif directive in ("CMD", "ENTRYPOINT") and cmd is None:
            cmd = value
    detail = f"base image {base_image}" if base_image else "Docker build"
    facts.deployment.append(
        {"kind": "dockerfile", "path": rel_path, "detail": detail, "services": []}
    )
    if cmd:
        facts.entrypoints.append(
            {
                "path": rel_path,
                "kind": "container",
                "detail": f"container command: {cmd}",
            }
        )


_TOP_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):\s*(#.*)?$")
_CHILD_KEY_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*(#.*)?$")


def _top_level_children(text: str, section: str) -> list[str]:
    """
    Names of the 2-space-indented child keys directly under a top-level
    `<section>:` key. Deliberately not a YAML parser (see module docstring):
    this only recognises the common two-space-indent style and reads key
    NAMES, never values — safe against YAML object/anchor tricks by
    construction, at the cost of missing unusually indented compose/CI files
    (surfaced as a warning by the caller when nothing is found).
    """
    lines = text.splitlines()
    names: list[str] = []
    in_section = False
    for line in lines:
        if not in_section:
            if re.match(rf"^{re.escape(section)}:\s*(#.*)?$", line):
                in_section = True
            continue
        if not line.strip():
            continue
        if _CHILD_KEY_RE.match(line):
            names.append(_CHILD_KEY_RE.match(line).group(1))
            continue
        if line.startswith(("  ", "\t")):
            continue  # deeper nesting under a service — skip
        break  # dedented back out of the section
    return names


def _parse_compose(path: Path, rel_path: str, facts: ManifestFacts) -> None:
    text = _read(path)
    if not text:
        return
    services = _top_level_children(text, "services")
    facts.deployment.append(
        {
            "kind": "compose",
            "path": rel_path,
            "detail": f"{len(services)} service(s)",
            "services": services,
        }
    )


def _parse_ci_workflow(path: Path, rel_path: str, facts: ManifestFacts) -> None:
    text = _read(path)
    if not text:
        return
    jobs = _top_level_children(text, "jobs")
    facts.deployment.append(
        {
            "kind": "ci_workflow",
            "path": rel_path,
            "detail": f"{len(jobs)} job(s)",
            "services": jobs,
        }
    )


def _parse_env_example(path: Path, facts: ManifestFacts) -> None:
    text = _read(path)
    if not text:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        # Defensive: never keep anything that looks like it captured a value.
        if name and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            facts.env_var_names.append(name)


_LOCKFILE_MANAGERS: tuple[tuple[str, str], ...] = (
    ("package-lock.json", "npm"),
    ("yarn.lock", "Yarn"),
    ("pnpm-lock.yaml", "pnpm"),
    ("poetry.lock", "Poetry"),
    ("Pipfile.lock", "Pipenv"),
)


def scan_manifests(root: Path, inv: Inventory) -> ManifestFacts:
    """Synchronous — callers run this via asyncio.to_thread."""
    facts = ManifestFacts()

    for name, manager in _LOCKFILE_MANAGERS:
        if (root / name).is_file():
            facts.package_managers.add(manager)

    for record in inv.files:
        rel = record.rel_path
        name = Path(rel).name
        rel_dir = str(Path(rel).parent) if Path(rel).parent != Path(".") else ""

        if name == "package.json":
            _parse_package_json(record.abs_path, rel_dir, facts)
        elif name == "pyproject.toml":
            _parse_pyproject(record.abs_path, rel_dir, facts)
        elif name in (
            "requirements.txt",
            "requirements-dev.txt",
            "requirements.dev.txt",
        ) or (name.startswith("requirements") and name.endswith(".txt")):
            _parse_requirements(record.abs_path, facts)
        elif _DOCKERFILE_RE.search(rel):
            _parse_dockerfile(record.abs_path, rel, facts)
        elif _COMPOSE_RE.search(rel):
            _parse_compose(record.abs_path, rel, facts)
        elif rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml")):
            _parse_ci_workflow(record.abs_path, rel, facts)
        elif name in ENV_EXAMPLE_NAMES:
            _parse_env_example(record.abs_path, facts)

    facts.env_var_names = sorted(set(facts.env_var_names))[:60]
    return facts
