"""Review API routes.

Thin HTTP layer for POST /api/review. It validates the request, orchestrates
the existing services (github -> diff -> ai), and maps domain errors to HTTP
status codes (CLAUDE.md §5, §6). No business logic lives here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.schemas.review import ReviewRequest, ReviewResponse
from app.services import ai_service, diff_service, github_service
from app.services.ai_service import AIAPIError, AIConfigError, AIResponseError
from app.services.github_service import (
    GitHubAPIError,
    InvalidPRUrlError,
    PullRequestNotFoundError,
    RateLimitError,
)

router = APIRouter(prefix="/api", tags=["review"])


@router.post("/review", response_model=ReviewResponse)
async def review_pull_request(request: ReviewRequest) -> ReviewResponse:
    """Run the AI review workflow for a public GitHub pull request URL.

    Pipeline (each step delegates to a service — see those modules):
      1. github_service.get_pull_request — fetch PR metadata + changed files.
      2. diff_service.prepare_diff       — filter/bound the diffs.
      3. ai_service.review_pull_request  — Groq review + schema validation.
    """
    # 1. Fetch PR data from GitHub.
    try:
        pr_data = await github_service.get_pull_request(request.pr_url)
    except InvalidPRUrlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except PullRequestNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    except RateLimitError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    except GitHubAPIError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    # 2. Process diffs (pure function; applies file/size limits).
    prepared = diff_service.prepare_diff(pr_data.files)

    # 3. AI review (handles the "nothing reviewable" case internally).
    try:
        return await ai_service.review_pull_request(
            prepared, pr_data.repository, pr_data.pull_request
        )
    except AIConfigError:
        # Server-side misconfiguration — do not expose which key is missing.
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The AI review service is not properly configured.",
        )
    except (AIAPIError, AIResponseError) as exc:
        # Upstream Groq failure, timeout, or malformed/invalid AI output.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))
