"""
Read-only GitHub Issues fetcher.

fetch_github_issues(owner, repo) -> list[Issue]
    Fetches open issues (excluding pull requests), caps the result,
    maps GitHub labels to our Priority enum.

Failure is non-fatal: the caller adds a warning to ScanResult.warnings
instead of failing the whole scan.
"""

import logging
import uuid

import httpx

from app.config import GITHUB_TOKEN
from app.models.contracts import Issue, Priority

log = logging.getLogger(__name__)

_ISSUES_CAP = 50  # maximum issues fetched from GitHub


def _scrub_token(text: str) -> str:
    if GITHUB_TOKEN:
        return text.replace(GITHUB_TOKEN, "***")
    return text


def _label_priority(labels: list[dict]) -> Priority:
    """Heuristic: map label names to Priority."""
    names = {lbl.get("name", "").lower() for lbl in labels}
    if names & {"critical", "high", "urgent", "bug: high", "severity: high"}:
        return "High"
    if names & {"low", "minor", "severity: low"}:
        return "Low"
    return "Medium"


async def fetch_github_issues(owner: str, repo: str) -> tuple[list[Issue], list[str]]:
    """
    Return (issues, warnings).
    *warnings* is non-empty only on partial or total failure.
    """
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/issues"
        f"?state=open&per_page={_ISSUES_CAP}"
    )
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=20.0, write=5.0, pool=5.0),
            follow_redirects=False,
        ) as client:
            resp = await client.get(url, headers=headers)
    except httpx.TransportError as exc:
        msg = _scrub_token(str(exc))
        log.warning("github/issues: transport error for %s/%s: %s", owner, repo, msg)
        return [], [f"Could not fetch GitHub Issues: {msg}"]

    if resp.status_code not in (200, 304):
        log.warning(
            "github/issues: unexpected status %s for %s/%s",
            resp.status_code,
            owner,
            repo,
        )
        return [], [
            f"GitHub Issues fetch returned status {resp.status_code}; issues omitted."
        ]

    try:
        raw_items = resp.json()
    except ValueError:
        return [], ["GitHub Issues response was not valid JSON; issues omitted."]

    issues: list[Issue] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        # Exclude pull requests
        if "pull_request" in item:
            continue

        issues.append(
            Issue(
                id=str(uuid.uuid4()),
                title=item.get("title", "(no title)"),
                description=(item.get("body") or "")[:2000],
                priority=_label_priority(item.get("labels", [])),
                source="github_issue",
                github_url=item.get("html_url"),
            )
        )

    return issues, []
