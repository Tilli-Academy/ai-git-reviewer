"""Pydantic models for the review API.

Defines the request model, the structured AI-finding model, and the response
model returned by POST /api/review. These models are the API contract shared
by the routes, the GitHub/diff services, and the Groq AI service.
See CLAUDE.md §6 and §7.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# A GitHub pull request URL, e.g. https://github.com/owner/repo/pull/123
# Captures owner, repo, and PR number so the github_service can reuse it.
GITHUB_PR_URL_RE = re.compile(
    r"^https?://github\.com/"
    r"(?P<owner>[\w.-]+)/"
    r"(?P<repo>[\w.-]+)/"
    r"pull/"
    r"(?P<number>\d+)"
    r"/?$"
)


class Severity(str, Enum):
    """Severity levels for a finding, highest to lowest."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(str, Enum):
    """The kind of issue a finding describes (CLAUDE.md §7)."""

    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    QUALITY = "quality"
    ERROR_HANDLING = "error-handling"
    EDGE_CASE = "edge-case"
    TESTING = "testing"


class ReviewRequest(BaseModel):
    """Incoming request body for POST /api/review."""

    pr_url: str = Field(
        ...,
        description="Public GitHub pull request URL, e.g. "
        "https://github.com/owner/repo/pull/123",
        examples=["https://github.com/owner/repo/pull/123"],
    )

    @field_validator("pr_url")
    @classmethod
    def validate_github_pr_url(cls, value: str) -> str:
        value = value.strip()
        if not GITHUB_PR_URL_RE.match(value):
            raise ValueError(
                "pr_url must be a valid GitHub pull request URL, e.g. "
                "https://github.com/owner/repo/pull/123"
            )
        return value


class RepositoryInfo(BaseModel):
    """Repository the pull request belongs to."""

    owner: str
    name: str
    full_name: str = Field(..., description="owner/name, e.g. 'octocat/Hello-World'")


class PullRequestInfo(BaseModel):
    """Metadata about the reviewed pull request."""

    number: int
    title: str
    author: str
    state: str = Field(..., description="PR state, e.g. 'open' or 'closed'")
    url: str


class ReviewFinding(BaseModel):
    """A single AI-generated review finding (CLAUDE.md §7).

    `line` is optional because not every finding maps to a specific line.
    `explanation` is included per CLAUDE.md §7 (why the issue matters), in
    addition to the short `description` and the actionable `suggestion`.
    """

    severity: Severity
    category: Category
    file: str
    line: Optional[int] = Field(
        default=None, description="1-based line number when available"
    )
    description: str = Field(..., description="What the issue is")
    explanation: str = Field(..., description="Why it matters")
    suggestion: str = Field(..., description="How to fix it")


class ReviewResponse(BaseModel):
    """Structured result returned by POST /api/review (CLAUDE.md §6)."""

    repository: RepositoryInfo
    pull_request: PullRequestInfo
    summary: str = Field(..., description="Overall natural-language PR review summary")
    total_findings: int = Field(default=0, ge=0)
    findings_by_severity: dict[Severity, int] = Field(default_factory=dict)
    findings: list[ReviewFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def populate_counts(self) -> "ReviewResponse":
        """Derive the counts from `findings` so callers set them in one place."""
        self.total_findings = len(self.findings)
        counts = {sev: 0 for sev in Severity}
        for finding in self.findings:
            counts[finding.severity] += 1
        self.findings_by_severity = counts
        return self
