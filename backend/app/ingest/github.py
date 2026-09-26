"""
GitHub repository ingest.

ingest_github(repo_url: str) -> tuple[Path, Path]
    Validates URL, checks repo metadata, downloads tarball (streamed),
    safe-extracts.  Returns (tmp_dir, extract_root) where tmp_dir is the
    top-level temp directory that owns everything (tarball + extracted tree).

The caller is responsible for cleaning up tmp_dir on failure.
On success, pass tmp_dir to RunStore so TTL expiry removes everything.

Security:
    - Only https://github.com/{owner}/{repo} URLs are accepted.
    - API requests go to api.github.com only (never user-supplied hosts).
    - Redirects are followed only to github.com / codeload.github.com.
    - GITHUB_TOKEN is never logged or echoed in error messages.
"""

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

import httpx

from app.config import (
    GITHUB_TOKEN,
    MAX_GITHUB_REPO_SIZE_KB,
    MAX_ZIP_SIZE_BYTES,
)
from app.errors import MedusaError
from app.ingest.safe_extract import extract_tarball

log = logging.getLogger(__name__)

# ── URL validation ────────────────────────────────────────────────────────────

_GITHUB_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[A-Za-z0-9][A-Za-z0-9_.-]*)/"
    r"(?P<repo>[A-Za-z0-9][A-Za-z0-9_.-]*)(?:\.git|/)?$"
)

# Strip trailing /tree/<branch> before matching
_TREE_STRIP_RE = re.compile(r"/tree/[^/].*$")

# Strict branch/ref name: alphanumeric, hyphens, dots, underscores, slashes
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")


def parse_github_location(url: str) -> tuple[str, str, str | None, str | None]:
    """
    Parse and validate a GitHub URL.

    Returns (owner, repo, ref, subdir). A /tree/<ref>/<path> suffix gives the
    branch or tag (first segment) and an optional subdirectory to scan (the
    rest). Branch names containing "/" are therefore not supported.
    Raises MedusaError(422) on invalid URLs.
    """
    url = url.strip().rstrip("/")

    ref: str | None = None
    subdir: str | None = None
    tree_match = re.search(r"/tree/([^/].*)$", url)
    if tree_match:
        raw = tree_match.group(1).rstrip("/")
        if not _REF_RE.match(raw) or ".." in raw.split("/"):
            raise MedusaError(
                422,
                "Branch, tag or folder name in the URL contains invalid characters. "
                "Please use the repository root URL.",
            )
        ref, _, rest = raw.partition("/")
        subdir = rest or None
        url = _TREE_STRIP_RE.sub("", url)

    m = _GITHUB_RE.match(url)
    if not m:
        raise MedusaError(
            422,
            "Please provide a valid public GitHub URL: "
            "https://github.com/{owner}/{repo}",
        )
    owner = m.group("owner")
    repo = m.group("repo").removesuffix(".git")
    return owner, repo, ref, subdir


def parse_github_url(url: str) -> tuple[str, str, str | None]:
    """Returns (owner, repo, ref). See parse_github_location for subdirectories."""
    owner, repo, ref, _ = parse_github_location(url)
    return owner, repo, ref


# Keep the old private name as an alias so existing tests don't break.
def _parse_github_url(url: str) -> tuple[str, str]:
    """Legacy wrapper — returns (owner, repo) only."""
    owner, repo, _ = parse_github_url(url)
    return owner, repo


def _auth_headers() -> dict[str, str]:
    if GITHUB_TOKEN:
        return {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    return {}


def _scrub_token(text: str) -> str:
    """Remove any accidental token leakage from error text."""
    if GITHUB_TOKEN:
        text = text.replace(GITHUB_TOKEN, "***")
    return text


def _is_allowed_redirect(url: str) -> bool:
    return url.startswith(("https://github.com/", "https://codeload.github.com/"))


# ── Main entry point ─────────────────────────────────────────────────────────


async def ingest_github(repo_url: str) -> tuple[Path, Path]:
    """
    Validate *repo_url*, fetch metadata, download tarball (streamed), extract.

    Returns (tmp_dir, extract_root) — tmp_dir is owned by caller.
    Raises MedusaError on any validation or network failure (tmp_dir already
    deleted before raising).
    """
    owner, repo, ref, subdir = parse_github_location(repo_url)

    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    ref_segment = f"/{ref}" if ref else ""
    tarball_url = f"https://api.github.com/repos/{owner}/{repo}/tarball{ref_segment}"

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        **_auth_headers(),
    }

    tmp_dir = Path(tempfile.mkdtemp(prefix="medusa_gh_"))
    tarball_path = tmp_dir / "repo.tar.gz"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
            follow_redirects=False,
        ) as client:
            # ── Metadata check ────────────────────────────────────────────────
            try:
                resp = await client.get(api_url, headers=headers)
            except httpx.TransportError as exc:
                raise MedusaError(
                    502, f"Could not reach GitHub API: {_scrub_token(str(exc))}"
                ) from exc

            if resp.status_code == 404:
                raise MedusaError(
                    422,
                    "Repository not found or is private. Only public repos are supported.",
                )
            if resp.status_code in (403, 429):
                reset = resp.headers.get("X-RateLimit-Reset", "")
                hint = f" (resets at {reset})" if reset else ""
                token_hint = (
                    "" if GITHUB_TOKEN else " Set GITHUB_TOKEN to raise the limit."
                )
                raise MedusaError(
                    429,
                    f"GitHub rate limit reached{hint}.{token_hint} Please try again later.",
                )
            if resp.status_code != 200:
                raise MedusaError(
                    502,
                    f"GitHub API returned an unexpected status ({resp.status_code}). Try again later.",
                )

            meta = resp.json()

            if meta.get("private"):
                raise MedusaError(
                    422,
                    "Repository is private. Only public repositories are supported.",
                )

            size_kb: int = meta.get("size", 0)
            if size_kb > MAX_GITHUB_REPO_SIZE_KB:
                size_mb = size_kb // 1024
                cap_mb = MAX_GITHUB_REPO_SIZE_KB // 1024
                raise MedusaError(
                    413,
                    f"Repository is ~{size_mb} MB; maximum is {cap_mb} MB. "
                    "Try a smaller repository.",
                )

            if meta.get("archived"):
                log.info("github: repo %s/%s is archived", owner, repo)

            # ── Download tarball (streamed with byte cap) ─────────────────────
            # Follow redirects manually to enforce allowed hosts, then stream.
            next_url: str = tarball_url
            max_redirects = 5
            download_cap = (
                MAX_ZIP_SIZE_BYTES * 10
            )  # compressed; ~1 GB when local cap is 100 MB

            for _ in range(max_redirects):
                try:
                    async with client.stream(
                        "GET", next_url, headers=headers
                    ) as dl_resp:
                        if dl_resp.status_code in (301, 302, 303, 307, 308):
                            location = dl_resp.headers.get("location", "")
                            if not _is_allowed_redirect(location):
                                raise MedusaError(
                                    502, "GitHub redirected to an unexpected host."
                                )
                            next_url = location
                            continue
                        if dl_resp.status_code == 404 and ref:
                            raise MedusaError(
                                422,
                                f"Branch or tag '{ref}' was not found. Branch names "
                                "containing '/' are not supported; use the repository "
                                "root URL instead.",
                            )
                        if dl_resp.status_code != 200:
                            raise MedusaError(
                                502,
                                f"Failed to download repository tarball (status {dl_resp.status_code}).",
                            )
                        # Stream to disk with byte cap enforced chunk-by-chunk
                        received = 0
                        with open(tarball_path, "wb") as fh:  # noqa: ASYNC230
                            async for chunk in dl_resp.aiter_bytes(65_536):
                                received += len(chunk)
                                if received > download_cap:
                                    raise MedusaError(
                                        413,
                                        "Repository download exceeded the size cap. Try a smaller repo.",
                                    )
                                fh.write(chunk)
                        break  # download complete
                except httpx.TransportError as exc:
                    raise MedusaError(
                        502, f"Failed to download repository: {_scrub_token(str(exc))}"
                    ) from exc
            else:
                raise MedusaError(502, "Too many redirects downloading the repository.")

        # ── Extract ───────────────────────────────────────────────────────────
        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir()
        # Extraction is CPU/disk bound: keep it off the event loop.
        repo_root = await asyncio.to_thread(extract_tarball, tarball_path, extract_dir)

        if ref:
            log.info("github: scanning ref=%s for %s/%s", ref, owner, repo)
        if subdir:
            target = (repo_root / subdir).resolve()
            if not target.is_relative_to(repo_root.resolve()) or not target.is_dir():
                raise MedusaError(
                    422, f"Folder '{subdir}' was not found in the repository."
                )
            repo_root = target

        return tmp_dir, repo_root

    except MedusaError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.exception("Unexpected error during GitHub ingest for %s/%s", owner, repo)
        raise MedusaError(
            502, f"Unexpected error fetching repository: {_scrub_token(str(exc))}"
        ) from exc
