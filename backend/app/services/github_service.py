"""GitHub REST API integration.

Responsibilities (CLAUDE.md §5):
  * Parse and validate GitHub pull request URLs.
  * Fetch PR metadata and the list of changed files (with patches).
  * Handle pagination, missing patches, invalid URLs, missing PRs, API
    errors and rate limits.

Design notes:
  * HTTP communication (the ``_fetch_*`` helpers) is kept separate from
    response parsing (the ``parse_*`` helpers) so each is simple and testable.
  * All GitHub responses and PR patches are UNTRUSTED input. We read fields
    defensively with ``.get`` and never execute anything we receive.
  * No token is hardcoded. ``GITHUB_TOKEN`` is read through ``core.config``
    (which loads the repository-root .env); when absent, requests are made
    unauthenticated (fine for the public MVP).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.core import config
from app.schemas.review import (
    GITHUB_PR_URL_RE,
    PullRequestInfo,
    RepositoryInfo,
)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_ACCEPT = "application/vnd.github+json"
GITHUB_API_VERSION = "2022-11-28"

REQUEST_TIMEOUT = httpx.Timeout(20.0)
FILES_PER_PAGE = 100
# Safety valve so a pathologically large PR can't trigger unbounded paging.
# The real file-count/size limits are applied later by diff_service (§5).
MAX_FILE_PAGES = 30


# --------------------------------------------------------------------------- #
# Errors — routes will map these to HTTP status codes later (CLAUDE.md §6).
# --------------------------------------------------------------------------- #
class GitHubError(Exception):
    """Base class for GitHub integration errors."""


class InvalidPRUrlError(GitHubError):
    """The supplied string is not a valid GitHub pull request URL."""


class PullRequestNotFoundError(GitHubError):
    """The repository or pull request does not exist / is not public."""


class RateLimitError(GitHubError):
    """The GitHub API rate limit has been exceeded."""


class GitHubAPIError(GitHubError):
    """A GitHub API or network error that is not covered above."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# Internal representation of a changed file (consumed by diff_service next).
# --------------------------------------------------------------------------- #
@dataclass
class ChangedFile:
    filename: str
    status: str  # added / modified / removed / renamed ...
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    patch: Optional[str] = None  # absent for binary files or very large diffs
    previous_filename: Optional[str] = None  # set for renames


@dataclass
class PullRequestData:
    """Everything the review pipeline needs from GitHub about one PR."""

    repository: RepositoryInfo
    pull_request: PullRequestInfo
    files: list[ChangedFile] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# URL parsing
# --------------------------------------------------------------------------- #
def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL into ``(owner, repo, pr_number)``.

    Reuses the same regex the Pydantic ``ReviewRequest`` validates against, so
    validation and parsing can never drift apart.
    """
    match = GITHUB_PR_URL_RE.match(pr_url.strip())
    if not match:
        raise InvalidPRUrlError(
            "Invalid GitHub pull request URL. Expected "
            "https://github.com/owner/repo/pull/123"
        )
    return match["owner"], match["repo"], int(match["number"])


# --------------------------------------------------------------------------- #
# HTTP communication
# --------------------------------------------------------------------------- #
def _get_token() -> Optional[str]:
    """Return the optional GitHub token (never logged)."""
    return config.get_github_token()


def _build_headers(token: Optional[str]) -> dict[str, str]:
    headers = {
        "Accept": GITHUB_ACCEPT,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _raise_for_response(response: httpx.Response) -> None:
    """Translate a non-success GitHub response into a domain error.

    Note the rate-limit check comes first: GitHub reports an exhausted rate
    limit as 403 with ``X-RateLimit-Remaining: 0`` (not 429 for the REST API).
    """
    if response.is_success:
        return

    remaining = response.headers.get("X-RateLimit-Remaining")
    if response.status_code in (403, 429) and remaining == "0":
        raise RateLimitError(
            "GitHub API rate limit exceeded. Try again later or configure a "
            "GITHUB_TOKEN to raise the limit."
        )
    if response.status_code == 404:
        raise PullRequestNotFoundError(
            "Pull request or repository not found. It may be private or the "
            "URL may be incorrect."
        )
    # Deliberately do not include the response body — it is untrusted and may
    # contain noise; we surface only the status code.
    raise GitHubAPIError(
        f"GitHub API returned an unexpected status {response.status_code}.",
        status_code=response.status_code,
    )


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    token: Optional[str],
    *,
    params: Optional[dict[str, Any]] = None,
) -> Any:
    """Perform one GET request and return parsed JSON, mapping errors."""
    try:
        response = await client.get(
            path, headers=_build_headers(token), params=params
        )
    except httpx.RequestError as exc:
        # Network-level failure — do not leak internals, just the reason type.
        raise GitHubAPIError(f"Failed to reach GitHub: {exc.__class__.__name__}")

    _raise_for_response(response)

    try:
        return response.json()
    except ValueError:
        raise GitHubAPIError("GitHub returned a malformed (non-JSON) response.")


async def _fetch_pr_metadata(
    client: httpx.AsyncClient, owner: str, repo: str, number: int, token: Optional[str]
) -> dict[str, Any]:
    data = await _get_json(client, f"/repos/{owner}/{repo}/pulls/{number}", token)
    if not isinstance(data, dict):
        raise GitHubAPIError("Unexpected PR metadata shape from GitHub.")
    return data


async def _fetch_changed_files(
    client: httpx.AsyncClient, owner: str, repo: str, number: int, token: Optional[str]
) -> list[dict[str, Any]]:
    """Fetch all changed files, following pagination up to a safety cap."""
    files: list[dict[str, Any]] = []
    for page in range(1, MAX_FILE_PAGES + 1):
        batch = await _get_json(
            client,
            f"/repos/{owner}/{repo}/pulls/{number}/files",
            token,
            params={"per_page": FILES_PER_PAGE, "page": page},
        )
        if not isinstance(batch, list):
            raise GitHubAPIError("Unexpected changed-files shape from GitHub.")
        files.extend(item for item in batch if isinstance(item, dict))
        # Last page reached when GitHub returns fewer than a full page.
        if len(batch) < FILES_PER_PAGE:
            break
    return files


# --------------------------------------------------------------------------- #
# Response parsing (untrusted input — read defensively)
# --------------------------------------------------------------------------- #
def parse_repository_info(pr_json: dict[str, Any]) -> RepositoryInfo:
    repo = (pr_json.get("base") or {}).get("repo") or {}
    owner = (repo.get("owner") or {}).get("login") or ""
    name = repo.get("name") or ""
    full_name = repo.get("full_name") or (f"{owner}/{name}" if owner and name else "")
    return RepositoryInfo(owner=owner, name=name, full_name=full_name)


def parse_pull_request_info(pr_json: dict[str, Any]) -> PullRequestInfo:
    return PullRequestInfo(
        number=int(pr_json.get("number") or 0),
        title=pr_json.get("title") or "",
        author=(pr_json.get("user") or {}).get("login") or "",
        state=pr_json.get("state") or "",
        url=pr_json.get("html_url") or "",
    )


def parse_changed_file(file_json: dict[str, Any]) -> ChangedFile:
    return ChangedFile(
        filename=file_json.get("filename") or "",
        status=file_json.get("status") or "",
        additions=int(file_json.get("additions") or 0),
        deletions=int(file_json.get("deletions") or 0),
        changes=int(file_json.get("changes") or 0),
        # `patch` is omitted by GitHub for binary files and very large diffs.
        patch=file_json.get("patch"),
        previous_filename=file_json.get("previous_filename"),
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def get_pull_request(
    pr_url: str,
    *,
    token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> PullRequestData:
    """Validate ``pr_url`` and return structured PR data from GitHub.

    Pass ``client`` to reuse/mocked an :class:`httpx.AsyncClient` (used in
    tests); otherwise one is created and closed here.
    """
    owner, repo, number = parse_pr_url(pr_url)
    token = token if token is not None else _get_token()

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(base_url=GITHUB_API_BASE, timeout=REQUEST_TIMEOUT)

    try:
        pr_json = await _fetch_pr_metadata(client, owner, repo, number, token)
        files_json = await _fetch_changed_files(client, owner, repo, number, token)
    finally:
        if owns_client:
            await client.aclose()

    return PullRequestData(
        repository=parse_repository_info(pr_json),
        pull_request=parse_pull_request_info(pr_json),
        files=[parse_changed_file(f) for f in files_json],
    )
