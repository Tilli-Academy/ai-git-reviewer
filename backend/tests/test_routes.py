"""API route tests (CLAUDE.md §13).

External services (GitHub, Groq) are mocked — no network or AI calls. We verify
the health endpoint, the happy path, the empty-diff path, and that each domain
error maps to the correct HTTP status code (§6).
"""

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.review import PullRequestInfo, RepositoryInfo, ReviewResponse
from app.services import ai_service, github_service
from app.services.ai_service import AIAPIError, AIConfigError
from app.services.github_service import (
    ChangedFile,
    GitHubAPIError,
    PullRequestData,
    PullRequestNotFoundError,
    RateLimitError,
)

client = TestClient(app)

VALID_URL = "https://github.com/octocat/repo/pull/7"

_REPO = RepositoryInfo(owner="octocat", name="repo", full_name="octocat/repo")
_PR = PullRequestInfo(
    number=7, title="Add feature", author="octocat", state="open", url=VALID_URL
)


def _pr_data(files=None):
    if files is None:
        files = [ChangedFile(filename="app/main.py", status="modified",
                             patch="@@ -1 +1 @@\n-old\n+new")]
    return PullRequestData(repository=_REPO, pull_request=_PR, files=files)


def _patch_github(monkeypatch, result=None, error=None):
    async def fake_get(pr_url, **kwargs):
        if error is not None:
            raise error
        return result if result is not None else _pr_data()
    monkeypatch.setattr(github_service, "get_pull_request", fake_get)


def _patch_ai(monkeypatch, result=None, error=None):
    async def fake_review(prepared, repository, pull_request, **kwargs):
        if error is not None:
            raise error
        return result if result is not None else ReviewResponse(
            repository=repository, pull_request=pull_request,
            summary="ok", findings=[],
        )
    monkeypatch.setattr(ai_service, "review_pull_request", fake_review)


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_review_success(monkeypatch):
    _patch_github(monkeypatch)
    _patch_ai(monkeypatch, result=ReviewResponse(
        repository=_REPO, pull_request=_PR, summary="Looks good",
        findings=[],
    ))
    resp = client.post("/api/review", json={"pr_url": VALID_URL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["repository"]["full_name"] == "octocat/repo"
    assert body["pull_request"]["number"] == 7
    assert body["summary"] == "Looks good"
    assert body["total_findings"] == 0


def test_review_empty_diff_returns_no_reviewable(monkeypatch):
    # Only a binary file -> diff_service yields nothing reviewable -> real
    # ai_service short-circuits without any Groq call.
    _patch_github(monkeypatch, result=_pr_data(
        files=[ChangedFile(filename="logo.png", status="added", patch=None)]
    ))
    resp = client.post("/api/review", json={"pr_url": VALID_URL})
    assert resp.status_code == 200
    assert resp.json()["total_findings"] == 0
    assert "no reviewable code changes" in resp.json()["summary"].lower()


# --------------------------------------------------------------------------- #
# Validation & error mapping (§6)
# --------------------------------------------------------------------------- #
def test_invalid_url_is_422():
    resp = client.post("/api/review", json={"pr_url": "https://example.com/not-a-pr"})
    assert resp.status_code == 422  # schema validation rejects before routing


def test_missing_body_is_422():
    resp = client.post("/api/review", json={})
    assert resp.status_code == 422


def test_pr_not_found_is_404(monkeypatch):
    _patch_github(monkeypatch, error=PullRequestNotFoundError("not found"))
    resp = client.post("/api/review", json={"pr_url": VALID_URL})
    assert resp.status_code == 404


def test_rate_limit_is_429(monkeypatch):
    _patch_github(monkeypatch, error=RateLimitError("rate limited"))
    resp = client.post("/api/review", json={"pr_url": VALID_URL})
    assert resp.status_code == 429


def test_github_api_error_is_502(monkeypatch):
    _patch_github(monkeypatch, error=GitHubAPIError("boom", status_code=500))
    resp = client.post("/api/review", json={"pr_url": VALID_URL})
    assert resp.status_code == 502


def test_ai_api_error_is_502(monkeypatch):
    _patch_github(monkeypatch)
    _patch_ai(monkeypatch, error=AIAPIError("groq down"))
    resp = client.post("/api/review", json={"pr_url": VALID_URL})
    assert resp.status_code == 502


def test_ai_config_error_is_500_and_hides_details(monkeypatch):
    _patch_github(monkeypatch)
    _patch_ai(monkeypatch, error=AIConfigError("GROQ_API_KEY is not configured."))
    resp = client.post("/api/review", json={"pr_url": VALID_URL})
    assert resp.status_code == 500
    # The specific missing key must NOT leak to the client.
    assert "GROQ_API_KEY" not in resp.json()["detail"]
