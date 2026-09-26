"""
Architecture generation pipeline.

Reuses the scan's already-extracted repository tree (ScanRecord.root) to build
a deterministic component graph and Mermaid diagram, entirely offline and
read-only. Model-assisted synthesis (Granite) is not wired into this build:
every report today has source in {curated, static} — "static_and_model" is
reserved for once that lands. A Granite outage therefore never affects this
pipeline at all yet; deterministic facts are always what is returned.

Every run ends with exactly one `done` event carrying the final
ArchitectureReport. Any failure ends in a visible `error` event.
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path

from app import config
from app.architecture import graph as graphmod
from app.architecture import inventory as inventorymod
from app.architecture import manifests as manifestsmod
from app.architecture import mermaid as mermaidmod
from app.architecture.curated import CuratedArtifactError, load_curated
from app.architecture.extract_python import FileFacts
from app.architecture.extract_python import extract as extract_python
from app.architecture.inventory import Inventory
from app.architecture.manifests import ManifestFacts
from app.models.contracts import (
    ArchitectureCoverage,
    ArchitectureReport,
    DataStore,
    DeploymentArtifact,
    Entrypoint,
    EvidenceRef,
    ExternalService,
    TechnologyStack,
)
from app.store import ArchitectureRun, RunStore, ScanRecord
from app.streaming import EventChannel

log = logging.getLogger(__name__)


async def start_architecture(store: RunStore, record: ScanRecord) -> ArchitectureRun:
    architecture_id = str(uuid.uuid4())
    is_demo = record.result.repo_source == "demo"
    report = ArchitectureReport(
        architecture_id=architecture_id,
        scan_id=record.scan_id,
        status="running",
        source="curated" if is_demo else "static",
        repo_source=record.result.repo_source,
        generated_at=time.time(),
    )
    run = ArchitectureRun(report=report, channel=EventChannel())
    store.add_architecture(run)
    record.architecture_id = architecture_id
    run.task = asyncio.create_task(_guarded(run, record))
    return run


async def _guarded(run: ArchitectureRun, record: ScanRecord) -> None:
    try:
        await asyncio.wait_for(_run(run, record), timeout=config.ARCH_TIMEOUT_S)
    except asyncio.CancelledError:
        run.report.status = "error"
        await run.channel.emit("medusa", "error", "Run cancelled.")
        raise
    except TimeoutError:
        log.warning("architecture: pipeline exceeded %ss", config.ARCH_TIMEOUT_S)
        if run.report.components:
            run.report.status = "partial"
            run.report.warnings.append(
                f"Analysis exceeded the {config.ARCH_TIMEOUT_S}s time budget; "
                "returning what was found so far."
            )
        else:
            run.report.status = "error"
        await run.channel.emit(
            "medusa",
            "error",
            f"Architecture analysis exceeded {config.ARCH_TIMEOUT_S}s and was stopped.",
        )
    except Exception:
        log.exception("architecture: pipeline crashed")
        run.report.status = "error"
        await run.channel.emit(
            "medusa",
            "error",
            "Unexpected error while generating the architecture report. Please try again.",
        )
    finally:
        run.report.log = list(run.channel.log)
        run.report.generated_at = time.time()
        await run.channel.done(run.report.model_dump_json())


async def _run(run: ArchitectureRun, record: ScanRecord) -> None:
    ch, report = run.channel, run.report
    await ch.emit(
        "architecture",
        "info",
        f"Starting architecture analysis (source: {report.repo_source}).",
    )

    if report.repo_source == "demo":
        await _run_curated(run, record)
        return

    root = record.root
    if root is None:
        report.status = "error"
        await ch.emit(
            "architecture",
            "error",
            "No extracted repository tree is available for this scan.",
        )
        return

    await ch.emit(
        "architecture",
        "info",
        "This repository's code is read as text and never executed.",
    )

    inv = await asyncio.to_thread(inventorymod.build_inventory, root)
    await ch.emit(
        "inventory",
        "info",
        f"Walked {inv.files_discovered} files; {inv.source_files_discovered} are source files "
        f"in a recognised language ({inv.source_files_supported} in a language this build parses).",
    )

    tier, limit_exceeded = inventorymod.choose_tier(inv)

    manifest_facts = await asyncio.to_thread(manifestsmod.scan_manifests, root, inv)
    await ch.emit(
        "manifests",
        "info",
        f"Found {len(manifest_facts.package_roots)} package manifest(s), "
        f"{len(manifest_facts.deployment)} deployment file(s).",
    )

    stack = _build_technology_stack(inv, manifest_facts)
    manifest_entrypoints = _manifest_entrypoints(manifest_facts)
    deployment = _build_deployment(manifest_facts)

    if tier == 3:
        report.status = "unavailable"
        report.technology_stack = stack
        report.entrypoints = manifest_entrypoints
        report.deployment = deployment
        report.coverage = _build_coverage(
            inv, tier, limit_exceeded, parsed=0, parse_failures=0
        )
        report.warnings.append(_tier3_warning(limit_exceeded))
        report.limitations.append(
            "No component graph was produced: the repository could not be inventoried "
            "within the configured limits, so any ranking would be based on an arbitrary "
            "subset and would likely be misleading."
        )
        report.narrowing_suggestions = _narrowing_suggestions()
        await ch.emit(
            "architecture",
            "result",
            "Repository too large for a full or partial component graph; see coverage for details.",
        )
        return

    parseable = [f for f in inv.files if f.language in inventorymod.SUPPORTED_LANGUAGES]
    parseable.sort(key=inventorymod.priority_key)
    budget = (
        config.ARCH_MAX_PARSED_FILES if tier == 1 else config.ARCH_TIER2_PARSED_FILES
    )
    to_parse = parseable[:budget]

    # Parsing is CPU-bound (up to ARCH_MAX_PARSED_FILES files of up to
    # ARCH_MAX_FILE_BYTES each): run it off the event loop so other requests and
    # live streams keep flowing, and stop at the run's time budget.
    file_facts, parse_failures, parse_stopped = await asyncio.to_thread(
        _parse_files, to_parse, time.monotonic() + config.ARCH_TIMEOUT_S
    )
    if parse_stopped:
        report.limitations.append(
            f"Parsing stopped at the {config.ARCH_TIMEOUT_S}s time budget; "
            "files after that point were not analysed."
        )

    await ch.emit(
        "parse",
        "info",
        f"Parsed {len(file_facts)} of {len(parseable)} supported source files.",
    )

    edges = graphmod.build_edges(file_facts)
    ranks = graphmod.pagerank(list(file_facts.keys()), edges)
    grouping = graphmod.group_components(inv.files, file_facts, ranks, manifest_facts)
    relationships = graphmod.build_relationships(edges, grouping.path_to_component)

    await ch.emit(
        "graph",
        "info",
        f"Built {len(file_facts)} node(s) and {len(edges)} edge(s); "
        f"grouped into {len(grouping.components)} component(s).",
    )

    entrypoints = _merge_entrypoints(
        manifest_entrypoints, _python_entrypoints(file_facts)
    )
    external_services = _build_external_services(file_facts)
    data_stores = _build_data_stores(file_facts)

    diagram = mermaidmod.generate_from_parts(grouping.components, relationships)
    problems = mermaidmod.validate(diagram)
    if problems:
        log.warning("architecture: generated diagram failed validation: %s", problems)
        diagram = ""
        report.warnings.append(
            "The generated diagram failed a safety check and was withheld; "
            "see the component list instead."
        )
    await ch.emit(
        "diagram",
        "info",
        f"Generated a {len(grouping.components)}-node diagram."
        if diagram
        else "No diagram was generated.",
    )

    report.technology_stack = stack
    report.entrypoints = entrypoints
    report.deployment = deployment
    report.components = grouping.components
    report.relationships = relationships
    report.external_services = external_services
    report.data_stores = data_stores
    report.mermaid = diagram
    report.summary = _build_summary(stack, grouping.components, inv)
    report.coverage = _build_coverage(
        inv, tier, limit_exceeded, parsed=len(file_facts), parse_failures=parse_failures
    )
    report.files_considered = [
        f.rel_path for f in inv.files[: config.ARCH_REPORT_PATH_SAMPLE]
    ]
    report.files_parsed = sorted(file_facts.keys())[: config.ARCH_REPORT_PATH_SAMPLE]
    skipped = sorted({f.rel_path for f in inv.files} - set(file_facts.keys()))
    report.files_skipped = skipped[: config.ARCH_REPORT_PATH_SAMPLE]

    unsupported = {
        lang: n
        for lang, n in inv.languages.items()
        if lang not in inventorymod.SUPPORTED_LANGUAGES
    }
    if unsupported:
        names = ", ".join(
            f"{lang} ({n})"
            for lang, n in sorted(unsupported.items(), key=lambda kv: -kv[1])[:5]
        )
        report.limitations.append(
            f"{names} file(s) were counted but not parsed: this build statically parses Python "
            "only. Components for those files are inferred from directory structure and "
            "manifests only, never from imports or routes."
        )
    report.limitations.append(
        "Relationships reflect static imports between repository files only; connections to "
        "external services and data stores are listed separately but not drawn as edges. "
        "Runtime dependency injection and dynamic imports are not captured."
    )

    if tier == 2:
        report.status = "partial"
        report.warnings.append(
            _tier2_warning(limit_exceeded, len(to_parse), len(parseable))
        )
        report.narrowing_suggestions = _narrowing_suggestions()
    else:
        report.status = "complete"

    await ch.emit(
        "architecture",
        "result",
        f"Architecture analysis {report.status}: {len(report.components)} component(s), "
        f"{len(report.relationships)} relationship(s).",
    )


async def _run_curated(run: ArchitectureRun, record: ScanRecord) -> None:
    ch, report = run.channel, run.report
    await ch.emit(
        "architecture",
        "info",
        "Loading the curated OptiLearn architecture (no model calls).",
    )
    try:
        curated = await asyncio.to_thread(load_curated, record.scan_id)
    except CuratedArtifactError as exc:
        log.error("architecture: curated artifact failed to load: %s", exc)
        report.status = "error"
        await ch.emit(
            "architecture",
            "error",
            f"The bundled OptiLearn architecture could not be loaded: {exc}",
        )
        return
    curated.architecture_id = report.architecture_id
    run.report = curated
    await ch.emit(
        "architecture",
        "result",
        f"Loaded curated OptiLearn architecture ({len(curated.components)} components, "
        f"{len(curated.relationships)} relationships).",
    )


# ── Report assembly helpers ───────────────────────────────────────────────────


def _parse_files(
    files: list[inventorymod.FileRecord], deadline: float
) -> tuple[dict[str, FileFacts], int, bool]:
    """
    Read and parse *files* (runs in a worker thread). Returns (facts by path,
    number of files that could not be read or parsed, whether the deadline
    stopped it early). Never raises for a bad file: extract() reports it.
    """
    file_facts: dict[str, FileFacts] = {}
    failures = 0
    for f in files:
        if time.monotonic() > deadline:
            return file_facts, failures, True
        text = _read_text(f.abs_path)
        if text is None:
            failures += 1
            continue
        facts = extract_python(text, f.rel_path)
        if not facts.parse_ok:
            failures += 1
            continue
        file_facts[f.rel_path] = facts
    return file_facts, failures, False


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text("utf-8", errors="replace")
    except OSError:
        return None


def _build_technology_stack(inv: Inventory, mf: ManifestFacts) -> TechnologyStack:
    languages = [
        lang for lang, _ in sorted(inv.languages.items(), key=lambda kv: -kv[1])
    ]
    unsupported = [
        lang for lang in languages if lang not in inventorymod.SUPPORTED_LANGUAGES
    ]
    return TechnologyStack(
        languages=languages,
        frameworks=sorted(mf.frameworks),
        build_systems=sorted(mf.build_systems),
        package_managers=sorted(mf.package_managers),
        test_frameworks=sorted(mf.test_frameworks),
        unsupported_languages=unsupported,
    )


def _manifest_entrypoints(mf: ManifestFacts) -> list[Entrypoint]:
    result = []
    for e in mf.entrypoints:
        path = e["path"]
        has_real_path = path not in (".", "") and "." in Path(path).name
        result.append(
            Entrypoint(
                path=path,
                kind=e["kind"],
                detail=e.get("detail", ""),
                evidence=[EvidenceRef(path=path)] if has_real_path else [],
            )
        )
    return result


def _python_entrypoints(file_facts: dict[str, FileFacts]) -> list[Entrypoint]:
    result = []
    for rel_path, facts in file_facts.items():
        if facts.is_asgi_entrypoint:
            result.append(
                Entrypoint(
                    path=rel_path,
                    kind="http_server",
                    detail="ASGI/WSGI application instance",
                    evidence=[EvidenceRef(path=rel_path)],
                )
            )
        if facts.is_main_entrypoint:
            result.append(
                Entrypoint(
                    path=rel_path,
                    kind="script",
                    detail="if __name__ == '__main__' entrypoint",
                    evidence=[EvidenceRef(path=rel_path)],
                )
            )
    return result


def _merge_entrypoints(a: list[Entrypoint], b: list[Entrypoint]) -> list[Entrypoint]:
    seen: set[tuple[str, str]] = set()
    merged = []
    for ep in [*a, *b]:
        key = (ep.path, ep.kind)
        if key in seen:
            continue
        seen.add(key)
        merged.append(ep)
    return merged


def _build_deployment(mf: ManifestFacts) -> list[DeploymentArtifact]:
    return [
        DeploymentArtifact(
            kind=d["kind"],
            path=d["path"],
            detail=d.get("detail", ""),
            services=d.get("services", []),
        )
        for d in mf.deployment
    ]


_SDK_LABELS: dict[str, str] = {
    "httpx": "HTTP client (httpx)",
    "requests": "HTTP client (requests)",
    "aiohttp": "HTTP client (aiohttp)",
    "openai": "OpenAI API",
    "anthropic": "Anthropic API",
    "stripe": "Stripe API",
    "twilio": "Twilio API",
    "sendgrid": "SendGrid API",
    "slack_sdk": "Slack API",
    "boto3": "AWS SDK (boto3)",
    "ibm_watsonx_ai": "IBM watsonx.ai",
    "google": "Google API client",
}


def _build_external_services(file_facts: dict[str, FileFacts]) -> list[ExternalService]:
    found: dict[str, list[str]] = {}
    for rel_path, facts in file_facts.items():
        for sdk in facts.external_sdks:
            found.setdefault(sdk, []).append(rel_path)
    services = []
    for sdk, paths in sorted(found.items()):
        services.append(
            ExternalService(
                name=_SDK_LABELS.get(sdk, sdk),
                detail=f"imported in {len(paths)} file(s)",
                evidence=[EvidenceRef(path=p) for p in sorted(paths)[:3]],
                confidence=0.7,
                assisted_by="deterministic",
            )
        )
    return services


_DB_LABELS: dict[str, tuple[str, str]] = {
    "sqlalchemy": ("SQLAlchemy", "relational"),
    "psycopg": ("PostgreSQL (psycopg)", "relational"),
    "psycopg2": ("PostgreSQL (psycopg2)", "relational"),
    "asyncpg": ("PostgreSQL (asyncpg)", "relational"),
    "aiosqlite": ("SQLite (aiosqlite)", "relational"),
    "sqlite3": ("SQLite", "relational"),
    "pymongo": ("MongoDB (pymongo)", "document"),
    "motor": ("MongoDB (motor)", "document"),
    "redis": ("Redis", "key_value"),
    "elasticsearch": ("Elasticsearch", "document"),
    "duckdb": ("DuckDB", "relational"),
}


def _build_data_stores(file_facts: dict[str, FileFacts]) -> list[DataStore]:
    found: dict[str, list[str]] = {}
    for rel_path, facts in file_facts.items():
        for db in facts.db_clients:
            found.setdefault(db, []).append(rel_path)
    stores = []
    for db, paths in sorted(found.items()):
        label, kind = _DB_LABELS.get(db, (db, "unknown"))
        stores.append(
            DataStore(
                name=label,
                kind=kind,
                detail=f"client imported in {len(paths)} file(s)",
                evidence=[EvidenceRef(path=p) for p in sorted(paths)[:3]],
                confidence=0.7,
                assisted_by="deterministic",
            )
        )
    return stores


def _build_coverage(
    inv: Inventory,
    tier: int,
    limit_exceeded: str | None,
    *,
    parsed: int,
    parse_failures: int,
) -> ArchitectureCoverage:
    supported = inv.source_files_supported
    considered = len(inv.files)
    return ArchitectureCoverage(
        files_discovered=inv.files_discovered,
        source_files_discovered=inv.source_files_discovered,
        source_files_supported=supported,
        files_considered=considered,
        files_parsed=parsed,
        files_skipped=max(0, considered - parsed),
        source_bytes=inv.source_bytes,
        source_lines=inv.source_lines,
        parse_rate=round(parsed / supported, 3) if supported else 0.0,
        parse_failures=parse_failures,
        tier=tier,  # type: ignore[arg-type]
        limit_exceeded=limit_exceeded,
        skipped_reasons=dict(inv.skipped_reasons),
        languages_parsed={"Python": parsed} if parsed else {},
        languages_not_parsed={
            lang: n
            for lang, n in inv.languages.items()
            if lang not in inventorymod.SUPPORTED_LANGUAGES
        },
    )


def _tier3_warning(limit_exceeded: str | None) -> str:
    if limit_exceeded == "ARCH_MAX_DISCOVERED_FILES":
        return (
            f"This repository exceeds the architecture analysis limit of "
            f"{config.ARCH_MAX_DISCOVERED_FILES:,} discovered files (the walk was stopped "
            "after reaching it)."
        )
    return "No source files in a language this build can analyse were found."


def _tier2_warning(
    limit_exceeded: str | None, parsed_count: int, supported_count: int
) -> str:
    pct = round(100 * parsed_count / supported_count) if supported_count else 0
    reason = {
        "ARCH_MAX_SOURCE_BYTES": f"total source exceeds {config.ARCH_MAX_SOURCE_BYTES:,} bytes",
        "ARCH_MAX_SOURCE_LINES": f"total source exceeds {config.ARCH_MAX_SOURCE_LINES:,} lines",
        "ARCH_MAX_PARSED_FILES": f"more than {config.ARCH_MAX_PARSED_FILES:,} supported source files were found",
        "no_supported_source_files": "no files in a language this build parses were found",
    }.get(limit_exceeded or "", "the repository exceeds the full-analysis budget")
    return f"Parsed {parsed_count} of {supported_count} supported source files ({pct}%) — {reason}."


def _narrowing_suggestions() -> list[str]:
    return [
        "Upload or link a single service or package instead of the whole repository.",
        "Link a subdirectory directly, e.g. https://github.com/owner/repo/tree/main/services/api",
        "Exclude generated and vendored directories (node_modules, dist, build, target, vendor) before zipping.",
        "Zip only the directories you want mapped — a smaller, focused archive produces a more accurate diagram.",
    ]


def _build_summary(stack: TechnologyStack, components: list, inv: Inventory) -> str:
    lang = stack.languages[0] if stack.languages else "an unrecognised language"
    fw = f" using {', '.join(stack.frameworks[:3])}" if stack.frameworks else ""
    return (
        f"A {lang} repository{fw}, statically inferred into {len(components)} "
        f"architecture-level component(s) from {inv.source_files_discovered} source file(s)."
    )
