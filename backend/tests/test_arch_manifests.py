"""Tests for app/architecture/manifests.py — declarative manifest parsing only."""

import json
from pathlib import Path

from app.architecture import inventory as inv_mod
from app.architecture import manifests as mf_mod


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _scan(tmp_path):
    inv = inv_mod.build_inventory(tmp_path)
    return mf_mod.scan_manifests(tmp_path, inv)


def test_package_json_dependencies_and_frameworks(tmp_path):
    _write(
        tmp_path,
        "package.json",
        json.dumps(
            {
                "name": "app",
                "dependencies": {"next": "16.0.0", "react": "19.0.0"},
                "devDependencies": {"vitest": "1.0.0"},
                "main": "index.js",
            }
        ),
    )
    facts = _scan(tmp_path)
    assert facts.frameworks == {"Next.js", "React"}
    assert facts.test_frameworks == {"Vitest"}
    assert "npm" in facts.package_managers
    assert facts.package_roots == {""}
    assert any(e["path"] == "index.js" for e in facts.entrypoints)


def test_package_json_workspaces_detected(tmp_path):
    _write(tmp_path, "package.json", json.dumps({"workspaces": ["packages/*"]}))
    facts = _scan(tmp_path)
    assert facts.workspaces == ["packages/*"]


def test_pyproject_toml_dependencies_and_scripts(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
[project]
name = "svc"
dependencies = ["fastapi>=0.100", "sqlalchemy==2.0.0"]

[project.scripts]
svc-cli = "svc.cli:main"

[build-system]
build-backend = "hatchling.build"
""",
    )
    facts = _scan(tmp_path)
    assert facts.frameworks == {"FastAPI", "SQLAlchemy"}
    assert facts.build_systems == {"Hatch"}
    assert any("svc-cli" in e["detail"] for e in facts.entrypoints)


def test_requirements_txt_split_on_version_specifiers(tmp_path):
    _write(
        tmp_path,
        "requirements.txt",
        "fastapi==0.100.0\n# comment\ncelery[redis]>=5\npytest\n",
    )
    facts = _scan(tmp_path)
    assert facts.frameworks == {"FastAPI", "Celery"}
    assert facts.test_frameworks == {"pytest"}
    assert "celery" in facts.dependencies


def test_dockerfile_extracts_base_image_and_cmd(tmp_path):
    _write(
        tmp_path,
        "Dockerfile",
        'FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nCMD ["uvicorn", "app.main:app"]\n',
    )
    facts = _scan(tmp_path)
    assert len(facts.deployment) == 1
    assert facts.deployment[0]["kind"] == "dockerfile"
    assert "python:3.12-slim" in facts.deployment[0]["detail"]
    assert any(e["kind"] == "container" for e in facts.entrypoints)


def test_compose_services_extracted_by_name_only(tmp_path):
    _write(
        tmp_path,
        "docker-compose.yml",
        "version: '3'\nservices:\n  api:\n    build: .\n  worker:\n    build: .\n    command: run\n",
    )
    facts = _scan(tmp_path)
    assert len(facts.deployment) == 1
    assert facts.deployment[0]["kind"] == "compose"
    assert set(facts.deployment[0]["services"]) == {"api", "worker"}


def test_ci_workflow_jobs_extracted(tmp_path):
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "name: CI\non: push\njobs:\n  backend:\n    runs-on: ubuntu-latest\n  frontend:\n    runs-on: ubuntu-latest\n",
    )
    facts = _scan(tmp_path)
    assert len(facts.deployment) == 1
    assert facts.deployment[0]["kind"] == "ci_workflow"
    assert set(facts.deployment[0]["services"]) == {"backend", "frontend"}


def test_env_example_yields_names_only_never_values(tmp_path):
    _write(
        tmp_path,
        ".env.example",
        "DATABASE_URL=postgres://should-not-leak\nSECRET_KEY=\n# comment\n",
    )
    facts = _scan(tmp_path)
    assert facts.env_var_names == ["DATABASE_URL", "SECRET_KEY"]
    assert not any("postgres" in v for v in facts.env_var_names)


def test_lockfile_presence_detects_package_manager(tmp_path):
    (tmp_path / "yarn.lock").write_text("", encoding="utf-8")
    facts = _scan(tmp_path)
    assert "Yarn" in facts.package_managers


def test_no_yaml_library_used_for_compose_or_ci():
    """A real YAML loader can execute !!python/object tags or expand a
    billion-laughs bomb; compose/CI parsing must never import one."""
    import ast

    src = Path(mf_mod.__file__).read_text("utf-8")
    tree = ast.parse(src)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "yaml" not in imported
