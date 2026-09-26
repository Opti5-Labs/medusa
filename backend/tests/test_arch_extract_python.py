"""Tests for app/architecture/extract_python.py — stdlib `ast` extraction only."""

from app.architecture.extract_python import extract


def test_imports_absolute_and_relative_with_level():
    src = "import os\nfrom app.services import db\nfrom . import util\nfrom ..core import config\n"
    facts = extract(src, "app/routes/users.py")
    assert facts.parse_ok is True
    modules = [(m, level) for m, level, _ in facts.imports]
    assert ("os", 0) in modules
    assert ("app.services", 0) in modules
    assert (None, 1) in modules  # from . import util
    assert ("core", 2) in modules


def test_defines_classes_and_functions():
    src = "class Foo:\n    def bar(self):\n        pass\n\ndef baz():\n    pass\n"
    facts = extract(src, "m.py")
    assert set(facts.defines) == {"Foo", "bar", "baz"}


def test_fastapi_decorator_route_detected():
    src = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        '@router.get("/users")\n'
        "async def list_users():\n"
        "    return []\n"
    )
    facts = extract(src, "routes/users.py")
    assert len(facts.routes) == 1
    assert facts.routes[0].method == "GET"
    assert facts.routes[0].path == "/users"


def test_plain_dict_get_call_is_not_mistaken_for_a_route():
    src = "d = {}\nx = d.get('key')\n"
    facts = extract(src, "m.py")
    assert facts.routes == []


def test_include_router_recorded():
    src = "from app.routes import users\napp.include_router(users.router)\n"
    facts = extract(src, "app/main.py")
    assert "users.router" in facts.router_includes


def test_asgi_entrypoint_detected():
    src = "from fastapi import FastAPI\napp = FastAPI()\n"
    facts = extract(src, "app/main.py")
    assert facts.is_asgi_entrypoint is True


def test_main_entrypoint_detected():
    src = "if __name__ == '__main__':\n    run()\n"
    facts = extract(src, "app/main.py")
    assert facts.is_main_entrypoint is True


def test_db_worker_and_external_sdk_imports_classified():
    src = "import sqlalchemy\nimport celery\nimport httpx\nimport openai\n"
    facts = extract(src, "app/services/x.py")
    assert facts.db_clients == {"sqlalchemy"}
    assert facts.workers == {"celery"}
    assert facts.external_sdks == {"httpx", "openai"}


def test_pydantic_base_model_detected_as_data_model():
    src = "from pydantic import BaseModel\n\nclass User(BaseModel):\n    id: int\n"
    facts = extract(src, "app/models/user.py")
    assert facts.is_data_model is True


def test_config_module_detected_by_filename():
    facts = extract("X = 1\n", "app/core/config.py")
    assert facts.is_config_module is True


def test_syntax_error_does_not_raise_and_is_reported():
    facts = extract("def broken(:\n", "bad.py")
    assert facts.parse_ok is False
    assert facts.error is not None
    assert facts.defines == []


def test_never_raises_on_arbitrary_garbage_bytes_decoded_as_text():
    garbage = bytes(range(256)).decode("utf-8", errors="replace")
    facts = extract(garbage, "garbage.py")
    assert facts.parse_ok in (True, False)  # must not raise either way
