"""
Deterministic Python extractor: stdlib `ast` only.

`ast.parse()` builds a syntax tree and never imports, executes, or evaluates
anything — the same module app/demo/optilearn.py already uses on bundled
source. A file that fails to parse is reported as such and skipped; it never
raises out of extract().
"""

import ast
from dataclasses import dataclass, field

# ── Classification tables ─────────────────────────────────────────────────────

_ROUTE_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "route", "websocket"}
)
_DB_IMPORT_MARKERS = frozenset(
    {
        "sqlalchemy",
        "psycopg",
        "psycopg2",
        "asyncpg",
        "aiosqlite",
        "sqlite3",
        "pymongo",
        "motor",
        "redis",
        "elasticsearch",
        "duckdb",
    }
)
_WORKER_IMPORT_MARKERS = frozenset(
    {"celery", "rq", "arq", "dramatiq", "apscheduler", "kafka", "pika", "taskiq"}
)
_EXTERNAL_SDK_MARKERS = frozenset(
    {
        "httpx",
        "requests",
        "aiohttp",
        "openai",
        "anthropic",
        "stripe",
        "twilio",
        "sendgrid",
        "slack_sdk",
        "boto3",
        "ibm_watsonx_ai",
        "google",
    }
)
_DATA_MODEL_BASE_MARKERS = (
    "BaseModel",
    "DeclarativeBase",
    "Base",
    "TypedDict",
    "models.Model",
)
_CONFIG_MODULE_NAMES = frozenset({"config.py", "settings.py", "conf.py"})


@dataclass
class RouteInfo:
    method: str
    path: str
    lineno: int


@dataclass
class FileFacts:
    rel_path: str
    parse_ok: bool = True
    error: str | None = None
    imports: list[tuple[str | None, int, int]] = field(
        default_factory=list
    )  # (dotted_module, level, lineno)
    defines: list[str] = field(default_factory=list)
    routes: list[RouteInfo] = field(default_factory=list)
    router_includes: list[str] = field(
        default_factory=list
    )  # raw unparsed argument expr
    is_asgi_entrypoint: bool = False
    is_main_entrypoint: bool = False
    db_clients: set[str] = field(default_factory=set)
    external_sdks: set[str] = field(default_factory=set)
    workers: set[str] = field(default_factory=set)
    is_config_module: bool = False
    is_data_model: bool = False


def _root_module(name: str) -> str:
    return name.split(".", 1)[0]


def _decorator_route(dec: ast.expr) -> RouteInfo | None:
    if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
        return None
    if dec.func.attr not in _ROUTE_METHODS:
        return None
    if (
        not dec.args
        or not isinstance(dec.args[0], ast.Constant)
        or not isinstance(dec.args[0].value, str)
    ):
        return None
    return RouteInfo(
        method=dec.func.attr.upper(), path=dec.args[0].value, lineno=dec.lineno
    )


def extract(text: str, rel_path: str) -> FileFacts:
    """Never raises. A syntax error yields parse_ok=False with the reason."""
    facts = FileFacts(rel_path=rel_path)
    try:
        tree = ast.parse(text, filename=rel_path)
    except (SyntaxError, ValueError) as exc:
        facts.parse_ok = False
        facts.error = f"{type(exc).__name__}: {exc}"
        return facts

    filename = rel_path.rsplit("/", 1)[-1]
    facts.is_config_module = filename in _CONFIG_MODULE_NAMES

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.imports.append((alias.name, 0, node.lineno))
                root = _root_module(alias.name)
                _classify_import(root, facts)
        elif isinstance(node, ast.ImportFrom):
            facts.imports.append((node.module, node.level, node.lineno))
            if node.module:
                _classify_import(_root_module(node.module), facts)
            if node.module == "pydantic_settings" or (
                node.module and "BaseSettings" in [a.name for a in node.names]
            ):
                facts.is_config_module = True
        elif isinstance(node, ast.ClassDef):
            facts.defines.append(node.name)
            bases_src = []
            for base in node.bases:
                try:
                    bases_src.append(ast.unparse(base))
                except (ValueError, RecursionError):
                    continue
            if any(
                marker in b for b in bases_src for marker in _DATA_MODEL_BASE_MARKERS
            ):
                facts.is_data_model = True
            for dec in node.decorator_list:
                try:
                    if "dataclass" in ast.unparse(dec):
                        facts.is_data_model = True
                except (ValueError, RecursionError):
                    pass
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            facts.defines.append(node.name)
            for dec in node.decorator_list:
                route = _decorator_route(dec)
                if route:
                    facts.routes.append(route)
                try:
                    dec_src = ast.unparse(dec)
                except (ValueError, RecursionError):
                    dec_src = ""
                if "task" in dec_src and (
                    "celery" in dec_src.lower()
                    or "shared_task" in dec_src
                    or "app.task" in dec_src
                ):
                    facts.workers.add("Celery")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("app", "application"):
                    try:
                        call_src = ast.unparse(node.value)
                    except (ValueError, RecursionError):
                        call_src = ""
                    if any(
                        f"{name}(" in call_src
                        for name in ("FastAPI", "Flask", "Starlette")
                    ):
                        facts.is_asgi_entrypoint = True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in ("include_router", "mount", "register_blueprint")
                and node.args
            ):
                try:
                    facts.router_includes.append(ast.unparse(node.args[0]))
                except (ValueError, RecursionError):
                    pass
        elif isinstance(node, ast.If):
            try:
                test_src = ast.unparse(node.test)
            except (ValueError, RecursionError):
                test_src = ""
            if "__name__" in test_src and "__main__" in test_src:
                facts.is_main_entrypoint = True

    return facts


def _classify_import(root: str, facts: FileFacts) -> None:
    if root in _DB_IMPORT_MARKERS:
        facts.db_clients.add(root)
    if root in _WORKER_IMPORT_MARKERS:
        facts.workers.add(root)
    if root in _EXTERNAL_SDK_MARKERS:
        facts.external_sdks.add(root)
