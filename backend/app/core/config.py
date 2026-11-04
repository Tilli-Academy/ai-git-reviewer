"""Centralized application configuration (CLAUDE.md §5, §9).

Loads environment variables from the repository-root ``.env`` file (via
python-dotenv) and exposes typed accessors for them. This is the single place
the backend reads configuration from, so secrets are handled consistently and
never hardcoded.

Secrets are never logged or echoed. The actual ``.env`` is git-ignored;
``.env.example`` documents the required variables with placeholders only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Repository root = four levels up from this file:
#   backend/app/core/config.py -> core -> app -> backend -> <repo root>
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = PROJECT_ROOT / ".env"
# Fallback location: many setups keep the env file alongside the backend it
# configures (and uvicorn is run from backend/). Repo-root .env takes
# precedence; backend/.env fills in anything not already set.
BACKEND_ENV_PATH = PROJECT_ROOT / "backend" / ".env"

# Load .env once at import. Guarded so the module still imports if python-dotenv
# is not installed (e.g. in minimal/CI contexts); values then come from the
# process environment. On a configured machine the package is present.
try:
    from dotenv import load_dotenv

    # Load root first so it wins; load_dotenv does not override existing vars.
    load_dotenv(dotenv_path=ENV_PATH)
    load_dotenv(dotenv_path=BACKEND_ENV_PATH)
except ImportError:  # pragma: no cover - python-dotenv missing
    pass


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _get(name: str) -> Optional[str]:
    """Return an env var, treating empty/whitespace as unset."""
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


# --- Groq (required for AI review) ----------------------------------------- #
def get_groq_api_key() -> Optional[str]:
    return _get("GROQ_API_KEY")


def get_groq_model() -> Optional[str]:
    return _get("GROQ_MODEL")


def require_groq_config() -> tuple[str, str]:
    """Return ``(api_key, model)`` or raise a clear ConfigError if missing."""
    api_key = get_groq_api_key()
    model = get_groq_model()
    missing = [
        name
        for name, value in (("GROQ_API_KEY", api_key), ("GROQ_MODEL", model))
        if not value
    ]
    if missing:
        raise ConfigError(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Set these in your .env at the repository root "
            "(copy .env.example and fill in real values)."
        )
    # Narrowed by the check above.
    return api_key, model  # type: ignore[return-value]


# --- GitHub (optional for the public-repository MVP) ----------------------- #
def get_github_token() -> Optional[str]:
    """Optional GitHub token; ``None`` means unauthenticated public access."""
    return _get("GITHUB_TOKEN")
