"""Service-layer unit tests (CLAUDE.md §13).

These are pure/offline tests — no network calls. GitHub and Groq access is
exercised separately with mocks. Covers diff_service and ai_service.
"""

import asyncio
import json

import httpx
import pytest

from app.core import config
from app.schemas.review import PullRequestInfo, RepositoryInfo, Severity
from app.services.ai_service import (
    AIResponseError,
    build_review_prompt,
    parse_ai_response,
    review_pull_request,
)
from app.services.diff_service import (
    classify_skip,
    prepare_diff,
)
from app.services.github_service import (
    GITHUB_API_BASE,
    ChangedFile,
    GitHubAPIError,
    InvalidPRUrlError,
    PullRequestNotFoundError,
    RateLimitError,
    get_pull_request,
    parse_changed_file,
    parse_pr_url,
    parse_pull_request_info,
    parse_repository_info,
)


def _file(filename, patch="@@ -1 +1 @@\n-old\n+new", status="modified", **kw):
    return ChangedFile(filename=filename, status=status, patch=patch, **kw)


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #
def test_classify_skip_flags_irrelevant_files():
    assert classify_skip(_file("logo.png")) == "binary or non-text file"
    assert classify_skip(_file("package-lock.json")) == "dependency lockfile"
    assert classify_skip(_file("dist/bundle.js")) == "generated/vendored/minified file"
    assert classify_skip(_file("app/x.min.js")) == "generated/vendored/minified file"
    assert classify_skip(_file("node_modules/dep/index.js")) == (
        "generated/vendored/minified file"
    )


def test_classify_skip_allows_source_files():
    assert classify_skip(_file("backend/app/main.py")) is None
    assert classify_skip(_file("frontend/src/App.jsx")) is None


def test_source_files_are_included():
    result = prepare_diff([_file("a.py"), _file("b.js")])
    assert result.included_count == 2
    assert result.total_files_changed == 2
    assert result.skipped_count == 0
    assert result.has_reviewable_changes is True


# --------------------------------------------------------------------------- #
# Missing patches / statuses
# --------------------------------------------------------------------------- #
def test_missing_patch_is_skipped():
    result = prepare_diff([_file("image_handler.py", patch=None)])
    assert result.included_count == 0
    assert result.skipped[0].reason.startswith("no patch available")


def test_rename_without_patch_is_reported_as_such():
    result = prepare_diff([_file("new_name.py", patch=None, status="renamed",
                                 previous_filename="old_name.py")])
    assert result.skipped[0].reason == "renamed without content changes"


def test_rename_with_patch_preserves_previous_filename():
    result = prepare_diff([_file("new.py", status="renamed",
                                 previous_filename="old.py")])
    assert result.files[0].previous_filename == "old.py"
    assert result.files[0].status == "renamed"


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #
def test_file_count_limit_is_enforced():
    files = [_file(f"f{i}.py") for i in range(5)]
    result = prepare_diff(files, max_files=2)
    assert result.included_count == 2
    assert result.limit_reached is True
    assert all("file limit reached" in s.reason for s in result.skipped)


def test_per_file_patch_is_truncated():
    big = "x" * 5000
    result = prepare_diff([_file("big.py", patch=big)], max_patch_chars=1000)
    assert result.files[0].truncated is True
    assert len(result.files[0].patch) == 1000
    assert result.truncated is True


def test_total_size_budget_stops_further_files():
    files = [_file(f"f{i}.py", patch="y" * 400) for i in range(10)]
    result = prepare_diff(files, max_total_chars=800, max_patch_chars=10000)
    assert result.total_patch_chars <= 800
    assert result.limit_reached is True


def test_empty_input_has_no_reviewable_changes():
    result = prepare_diff([])
    assert result.has_reviewable_changes is False
    assert result.total_files_changed == 0


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_require_groq_config_raises_when_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    with pytest.raises(config.ConfigError) as exc:
        config.require_groq_config()
    assert "GROQ_API_KEY" in str(exc.value)
    assert "GROQ_MODEL" in str(exc.value)


def test_require_groq_config_returns_values_when_present(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_MODEL", "test-model")
    assert config.require_groq_config() == ("test-key", "test-model")


def test_blank_env_var_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "   ")
    assert config.get_groq_api_key() is None


# --------------------------------------------------------------------------- #
# ai_service — prompt construction & response parsing (offline)
# --------------------------------------------------------------------------- #
_REPO = RepositoryInfo(owner="octocat", name="repo", full_name="octocat/repo")
_PR = PullRequestInfo(
    number=7, title="Add feature", author="octocat", state="open",
    url="https://github.com/octocat/repo/pull/7",
)


def _prepared(patch="@@ -1 +1 @@\n-old\n+new", filename="app/main.py"):
    return prepare_diff([ChangedFile(filename=filename, status="modified", patch=patch)])


def test_prompt_fences_untrusted_diff_and_lists_files():
    prepared = _prepared()
    messages = build_review_prompt(prepared, _REPO, _PR)
    system, user = messages[0], messages[1]
    assert system["role"] == "system"
    # Injection defense + grounding instructions present in system prompt.
    assert "UNTRUSTED DATA" in system["content"]
    assert "Do NOT invent file names" in system["content"]
    # Diff content lives inside the data fence in the user message.
    assert "<<<BEGIN_UNTRUSTED_DIFF>>>" in user["content"]
    assert "<<<END_UNTRUSTED_DIFF>>>" in user["content"]
    assert "app/main.py" in user["content"]


def test_parse_valid_response_builds_review_response():
    prepared = _prepared()
    raw = json.dumps({
        "summary": "One issue found.",
        "findings": [{
            "severity": "high", "category": "security", "file": "app/main.py",
            "line": 2, "description": "d", "explanation": "e", "suggestion": "s",
        }],
    })
    resp = parse_ai_response(raw, prepared, _REPO, _PR)
    assert resp.total_findings == 1
    assert resp.findings_by_severity[Severity.HIGH] == 1
    assert resp.repository.full_name == "octocat/repo"  # trusted, not from model
    assert resp.pull_request.number == 7


def test_parse_drops_findings_for_unknown_files():
    prepared = _prepared(filename="app/main.py")
    raw = json.dumps({
        "summary": "x",
        "findings": [{
            "severity": "low", "category": "bug", "file": "does/not/exist.py",
            "line": 1, "description": "d", "explanation": "e", "suggestion": "s",
        }],
    })
    resp = parse_ai_response(raw, prepared, _REPO, _PR)
    assert resp.total_findings == 0  # hallucinated file dropped


def test_parse_drops_invalid_severity():
    prepared = _prepared()
    raw = json.dumps({
        "summary": "x",
        "findings": [{
            "severity": "catastrophic", "category": "bug", "file": "app/main.py",
            "description": "d", "explanation": "e", "suggestion": "s",
        }],
    })
    resp = parse_ai_response(raw, prepared, _REPO, _PR)
    assert resp.total_findings == 0


def test_parse_malformed_json_raises():
    with pytest.raises(AIResponseError):
        parse_ai_response("not json{", _prepared(), _REPO, _PR)


def test_parse_empty_response_raises():
    with pytest.raises(AIResponseError):
        parse_ai_response("", _prepared(), _REPO, _PR)


def test_parse_empty_findings_sets_no_issues_summary():
    raw = json.dumps({"summary": "", "findings": []})
    resp = parse_ai_response(raw, _prepared(), _REPO, _PR)
    assert resp.total_findings == 0
    assert "no potential issues" in resp.summary.lower()


def test_review_skips_ai_call_when_nothing_reviewable():
    # Only a binary file -> no reviewable changes -> no client needed.
    prepared = prepare_diff([ChangedFile(filename="logo.png", status="added", patch=None)])
    resp = asyncio.run(review_pull_request(prepared, _REPO, _PR))  # no client passed
    assert resp.total_findings == 0
    assert "no reviewable code changes" in resp.summary.lower()


def test_review_uses_injected_client_and_parses():
    prepared = _prepared()

    class _FakeMessage:
        content = json.dumps({
            "summary": "ok",
            "findings": [{
                "severity": "medium", "category": "quality", "file": "app/main.py",
                "line": 1, "description": "d", "explanation": "e", "suggestion": "s",
            }],
        })

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeCompletion:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        async def create(self, **kwargs):
            # Model and JSON response_format are forwarded.
            assert kwargs["model"] == "test-model"
            assert kwargs["response_format"] == {"type": "json_object"}
            return _FakeCompletion()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    resp = asyncio.run(review_pull_request(
        prepared, _REPO, _PR, client=_FakeClient(), model="test-model",
    ))
    assert resp.total_findings == 1
    assert resp.findings[0].severity == Severity.MEDIUM


# --------------------------------------------------------------------------- #
# github_service — HTTP via httpx.MockTransport (no network)
# --------------------------------------------------------------------------- #
_GH_URL = "https://github.com/octocat/repo/pull/7"

_PR_METADATA = {
    "number": 7,
    "title": "Add feature",
    "state": "open",
    "html_url": _GH_URL,
    "user": {"login": "octocat"},
    "base": {"repo": {
        "name": "repo",
        "full_name": "octocat/repo",
        "owner": {"login": "octocat"},
    }},
}


def _run_get_pr(handler, url=_GH_URL):
    """Drive get_pull_request against a MockTransport-backed async client."""
    async def _inner():
        async with httpx.AsyncClient(
            base_url=GITHUB_API_BASE, transport=httpx.MockTransport(handler)
        ) as client:
            return await get_pull_request(url, client=client, token=None)
    return asyncio.run(_inner())


# --- URL parsing ----------------------------------------------------------- #
def test_parse_pr_url_valid():
    assert parse_pr_url("https://github.com/octocat/repo/pull/7") == (
        "octocat", "repo", 7
    )


def test_parse_pr_url_invalid_raises():
    with pytest.raises(InvalidPRUrlError):
        parse_pr_url("https://example.com/not-a-pr")


def test_get_pull_request_invalid_url_raises():
    # Fails before any HTTP call, so the handler should never run.
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("HTTP should not be attempted for a bad URL")
    with pytest.raises(InvalidPRUrlError):
        _run_get_pr(handler, url="https://example.com/x")


# --- Defensive parsing of untrusted responses ------------------------------ #
def test_parse_repository_info_handles_missing_fields():
    repo = parse_repository_info({})  # nothing present
    assert repo.owner == "" and repo.name == "" and repo.full_name == ""


def test_parse_pull_request_info_handles_missing_fields():
    pr = parse_pull_request_info({})
    assert pr.number == 0 and pr.title == "" and pr.author == ""


def test_parse_changed_file_without_patch():
    cf = parse_changed_file({"filename": "logo.png", "status": "added"})
    assert cf.patch is None
    assert cf.additions == 0


# --- Successful fetch (metadata + changed files) --------------------------- #
def test_get_pull_request_success():
    def handler(request):
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json=[
                {"filename": "app/main.py", "status": "modified",
                 "additions": 1, "deletions": 1, "changes": 2,
                 "patch": "@@ -1 +1 @@\n-a\n+b"},
                {"filename": "logo.png", "status": "added"},  # no patch (binary)
            ])
        return httpx.Response(200, json=_PR_METADATA)

    data = _run_get_pr(handler)
    assert data.repository.full_name == "octocat/repo"
    assert data.pull_request.number == 7
    assert data.pull_request.author == "octocat"
    assert len(data.files) == 2
    assert data.files[0].patch is not None
    assert data.files[1].patch is None  # missing patch preserved as None


# --- Pagination ------------------------------------------------------------ #
def test_get_pull_request_follows_pagination():
    # Page 1 returns a full page (100) -> a second page is requested;
    # page 2 returns a short page -> paging stops.
    def handler(request):
        if request.url.path.endswith("/files"):
            page = request.url.params.get("page")
            if page == "1":
                return httpx.Response(200, json=[
                    {"filename": f"f{i}.py", "status": "modified", "patch": "@@"}
                    for i in range(100)
                ])
            return httpx.Response(200, json=[
                {"filename": "last.py", "status": "modified", "patch": "@@"}
            ])
        return httpx.Response(200, json=_PR_METADATA)

    data = _run_get_pr(handler)
    assert len(data.files) == 101
    assert data.files[-1].filename == "last.py"


# --- HTTP error mapping ---------------------------------------------------- #
def test_get_pull_request_404_maps_to_not_found():
    def handler(request):
        return httpx.Response(404, json={"message": "Not Found"})
    with pytest.raises(PullRequestNotFoundError):
        _run_get_pr(handler)


def test_get_pull_request_rate_limit_maps_to_rate_limit():
    def handler(request):
        return httpx.Response(
            403, headers={"X-RateLimit-Remaining": "0"},
            json={"message": "API rate limit exceeded"},
        )
    with pytest.raises(RateLimitError):
        _run_get_pr(handler)


def test_get_pull_request_other_status_maps_to_api_error():
    def handler(request):
        return httpx.Response(500, json={"message": "server error"})
    with pytest.raises(GitHubAPIError) as exc:
        _run_get_pr(handler)
    assert exc.value.status_code == 500


# --- Invalid / malformed responses ----------------------------------------- #
def test_get_pull_request_non_json_body_maps_to_api_error():
    def handler(request):
        return httpx.Response(200, text="this is not json")
    with pytest.raises(GitHubAPIError):
        _run_get_pr(handler)


def test_get_pull_request_unexpected_metadata_shape_raises():
    # Metadata endpoint returns a list instead of an object.
    def handler(request):
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=["unexpected"])
    with pytest.raises(GitHubAPIError):
        _run_get_pr(handler)
