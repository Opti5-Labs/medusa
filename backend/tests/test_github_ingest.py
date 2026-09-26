"""
Tests for ingest/github.py — URL validation, SSRF guard, error mapping.
All tests are fully offline (httpx mocked with respx or MockTransport).
"""

import io
import tarfile
import tempfile
from pathlib import Path
from typing import ClassVar
from unittest import mock

import httpx
import pytest

from app.errors import MedusaError
from app.ingest.github import _parse_github_url, ingest_github, parse_github_url

# ── URL parsing ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("github.com/owner/repo", ("owner", "repo")),  # scheme is optional
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://github.com/owner/repo/", ("owner", "repo")),
        ("https://github.com/owner/repo/tree/main", ("owner", "repo")),
        ("https://github.com/owner/my-repo_1.0", ("owner", "my-repo_1.0")),
    ],
)
def test_valid_github_urls(url, expected):
    assert _parse_github_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/repo",  # no https
        "https://evil.com/owner/repo",  # wrong host
        "https://github.com/owner/repo/../../etc",  # path traversal
        "https://user:pass@github.com/owner/repo",  # userinfo
        "https://github.com:8080/owner/repo",  # port
        "https://github.com/",  # no owner/repo
        "https://github.com/owner",  # no repo
        "ftp://github.com/owner/repo",  # wrong scheme
        "https://192.168.1.1/owner/repo",  # IP
        "",  # empty
        "not a url",  # garbage
    ],
)
def test_invalid_github_urls_rejected(url):
    with pytest.raises(MedusaError) as exc_info:
        _parse_github_url(url)
    assert exc_info.value.status == 422


# ── ingest_github with mocked httpx ──────────────────────────────────────────


def _make_tarball_bytes(files: dict[str, str]) -> bytes:
    """Create an in-memory .tar.gz with the given files."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _meta_response(
    size_kb: int = 100, private: bool = False, archived: bool = False
) -> dict:
    return {"size": size_kb, "private": private, "archived": archived}


class _MockTransport(httpx.AsyncBaseTransport):
    """Simple mock that returns pre-configured responses per URL prefix."""

    def __init__(self, routes: dict):
        # routes = { url_substring: httpx.Response }
        self._routes = routes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for pattern, response in self._routes.items():
            if pattern in url:
                return response
        return httpx.Response(404, json={"message": "Not Found"})


async def test_ingest_github_happy_path():
    """Happy path: metadata OK + tarball downloaded and extracted."""
    tarball = _make_tarball_bytes({"owner-repo-abc/main.py": "x = 1\n"})

    class _StreamResp:
        status_code = 200
        headers: ClassVar[dict] = {}

        async def aiter_bytes(self, chunk_size=65536):
            yield tarball

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    class _MockClient:
        async def get(self, url, **kwargs):
            return httpx.Response(200, json=_meta_response())

        def stream(self, method, url, **kwargs):
            return _StreamResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    with mock.patch("app.ingest.github.httpx.AsyncClient", return_value=_MockClient()):
        tmp_dir, root = await ingest_github("https://github.com/owner/repo")
        assert root.is_dir()
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


async def test_ingest_github_not_found():
    """404 from GitHub API -> MedusaError 422."""

    async def _get(url, **kwargs):
        return httpx.Response(404, json={"message": "Not Found"})

    with mock.patch("app.ingest.github.httpx.AsyncClient") as MockClient:
        inst = mock.AsyncMock()
        inst.get = _get
        MockClient.return_value.__aenter__.return_value = inst
        with pytest.raises(MedusaError) as exc_info:
            await ingest_github("https://github.com/owner/repo")
        assert exc_info.value.status == 422
        assert (
            "not found" in exc_info.value.message.lower()
            or "private" in exc_info.value.message.lower()
        )


async def test_ingest_github_private_repo():
    """Private repo -> MedusaError 422."""

    async def _get(url, **kwargs):
        return httpx.Response(200, json=_meta_response(private=True))

    with mock.patch("app.ingest.github.httpx.AsyncClient") as MockClient:
        inst = mock.AsyncMock()
        inst.get = _get
        MockClient.return_value.__aenter__.return_value = inst
        with pytest.raises(MedusaError) as exc_info:
            await ingest_github("https://github.com/owner/repo")
        assert exc_info.value.status == 422
        assert "private" in exc_info.value.message.lower()


async def test_ingest_github_oversize():
    """Repo over size limit -> MedusaError 413."""

    async def _get(url, **kwargs):
        return httpx.Response(200, json=_meta_response(size_kb=60 * 1024))  # 60 MB

    with mock.patch("app.ingest.github.httpx.AsyncClient") as MockClient:
        inst = mock.AsyncMock()
        inst.get = _get
        MockClient.return_value.__aenter__.return_value = inst
        with pytest.raises(MedusaError) as exc_info:
            await ingest_github("https://github.com/owner/repo")
        assert exc_info.value.status == 413
        assert "mb" in exc_info.value.message.lower()


async def test_ingest_github_rate_limited():
    """GitHub 429 -> MedusaError 429."""

    async def _get(url, **kwargs):
        return httpx.Response(
            429,
            json={"message": "rate limit exceeded"},
            headers={"X-RateLimit-Reset": "1234567890"},
        )

    with mock.patch("app.ingest.github.httpx.AsyncClient") as MockClient:
        inst = mock.AsyncMock()
        inst.get = _get
        MockClient.return_value.__aenter__.return_value = inst
        with pytest.raises(MedusaError) as exc_info:
            await ingest_github("https://github.com/owner/repo")
        assert exc_info.value.status == 429


async def test_token_not_in_error_message(monkeypatch):
    """GITHUB_TOKEN must never appear in any error message."""
    monkeypatch.setattr("app.ingest.github.GITHUB_TOKEN", "super_secret_token_abc123")

    async def _get(url, **kwargs):
        raise httpx.TransportError("Connection failed: super_secret_token_abc123")

    with mock.patch("app.ingest.github.httpx.AsyncClient") as MockClient:
        inst = mock.AsyncMock()
        inst.get = _get
        MockClient.return_value.__aenter__.return_value = inst
        with pytest.raises(MedusaError) as exc_info:
            await ingest_github("https://github.com/owner/repo")
        assert "super_secret_token_abc123" not in exc_info.value.message


# ── parse_github_url (public, returns ref) ────────────────────────────────────


def test_parse_github_url_with_branch():
    owner, repo, ref = parse_github_url("https://github.com/owner/repo/tree/main")
    assert owner == "owner"
    assert repo == "repo"
    assert ref == "main"


def test_parse_github_url_no_branch():
    owner, repo, ref = parse_github_url("https://github.com/owner/repo")
    assert owner == "owner"
    assert repo == "repo"
    assert ref is None


# ── Temp dir cleanup tests ────────────────────────────────────────────────────


async def test_no_temp_dir_leak_on_404():
    """After a GitHub 404 error no medusa_gh_* dir remains."""
    before = set(Path(tempfile.gettempdir()).glob("medusa_gh_*"))

    async def _get(url, **kwargs):
        return httpx.Response(404, json={"message": "Not Found"})

    with mock.patch("app.ingest.github.httpx.AsyncClient") as MockClient:
        inst = mock.AsyncMock()
        inst.get = _get
        MockClient.return_value.__aenter__.return_value = inst
        with pytest.raises(MedusaError):
            await ingest_github("https://github.com/owner/repo")

    after = set(Path(tempfile.gettempdir()).glob("medusa_gh_*"))
    assert after == before, f"Leaked temp dirs: {after - before}"


async def test_no_temp_dir_leak_on_oversize(monkeypatch):
    """After a size-limit rejection no medusa_gh_* dir remains."""
    monkeypatch.setattr("app.ingest.github.MAX_GITHUB_REPO_SIZE_KB", 1)  # 1 KB cap
    before = set(Path(tempfile.gettempdir()).glob("medusa_gh_*"))

    async def _get(url, **kwargs):
        return httpx.Response(200, json={"size": 9999, "private": False})

    with mock.patch("app.ingest.github.httpx.AsyncClient") as MockClient:
        inst = mock.AsyncMock()
        inst.get = _get
        MockClient.return_value.__aenter__.return_value = inst
        with pytest.raises(MedusaError) as exc_info:
            await ingest_github("https://github.com/owner/repo")
        assert exc_info.value.status == 413

    after = set(Path(tempfile.gettempdir()).glob("medusa_gh_*"))
    assert after == before, f"Leaked temp dirs: {after - before}"


async def test_streaming_byte_cap_aborts(monkeypatch):
    """Tarball download aborts with 413 when streaming past the byte cap."""
    # Set a tiny download cap so a small payload triggers it
    monkeypatch.setattr(
        "app.ingest.github.MAX_ZIP_SIZE_BYTES", 1
    )  # cap = 1 * 10 = 10 bytes

    meta_ok = {"size": 1, "private": False}
    big_payload = b"X" * 100  # 100 bytes > 10 byte cap

    class _StreamResp:
        status_code = 200
        headers: ClassVar[dict] = {}

        async def aiter_bytes(self, chunk_size=65536):
            yield big_payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    class _MockClientInst:
        async def get(self, url, **kwargs):
            return httpx.Response(200, json=meta_ok)

        def stream(self, method, url, **kwargs):
            return _StreamResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    before = set(Path(tempfile.gettempdir()).glob("medusa_gh_*"))

    with mock.patch(
        "app.ingest.github.httpx.AsyncClient", return_value=_MockClientInst()
    ):
        with pytest.raises(MedusaError) as exc_info:
            await ingest_github("https://github.com/owner/repo")
        assert exc_info.value.status == 413
        assert "size cap" in exc_info.value.message.lower()

    after = set(Path(tempfile.gettempdir()).glob("medusa_gh_*"))
    assert after == before, f"Leaked temp dirs after stream abort: {after - before}"
