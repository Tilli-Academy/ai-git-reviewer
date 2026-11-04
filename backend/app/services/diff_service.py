"""Diff processing / preparation.

Takes the raw ``ChangedFile`` objects from ``github_service`` and turns them
into a clean, bounded, structured payload that ``ai_service`` can safely send
to Groq (CLAUDE.md §5).

What it does:
  * Filters files that add no review value (images, binaries, lockfiles,
    generated/minified/vendored files).
  * Handles added / modified / removed / renamed files and files whose patch
    GitHub did not return (binary or too large).
  * Enforces configurable limits on the number of files and the total diff
    size, truncating or skipping gracefully instead of failing.
  * Records exactly what was skipped and why, so the result is transparent.

Security: every patch is treated strictly as DATA. This module only measures,
slices and copies patch strings — it never interprets, executes, or acts on
their contents. Prompt-injection defense happens in ai_service.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.services.github_service import ChangedFile

# --------------------------------------------------------------------------- #
# Configurable limits. Defaults are overridable via env (wired through
# core/config.py later) or per-call arguments to prepare_diff().
# --------------------------------------------------------------------------- #
DEFAULT_MAX_FILES = int(os.getenv("REVIEW_MAX_FILES", "50"))
DEFAULT_MAX_TOTAL_DIFF_CHARS = int(os.getenv("REVIEW_MAX_TOTAL_DIFF_CHARS", "60000"))
DEFAULT_MAX_PATCH_CHARS = int(os.getenv("REVIEW_MAX_PATCH_CHARS", "20000"))
# Don't bother including a patch truncated below this — too little context.
_MIN_USEFUL_PATCH_CHARS = 400

# File extensions whose contents are not reviewable source text.
_BINARY_EXTS = frozenset(
    {
        # images
        "png", "jpg", "jpeg", "gif", "bmp", "ico", "svg", "webp", "tiff",
        # documents / archives
        "pdf", "zip", "gz", "tgz", "tar", "rar", "7z", "bz2", "xz",
        # compiled / binary
        "exe", "dll", "so", "dylib", "bin", "class", "jar", "war", "o", "a",
        "pyc", "pyd", "wasm",
        # media
        "mp3", "mp4", "mov", "avi", "mkv", "wav", "flac", "webm", "ogg",
        # fonts
        "woff", "woff2", "ttf", "eot", "otf",
        # misc binary-ish
        "lock", "pdf", "psd", "ai", "sketch",
    }
)

# Dependency lockfiles — huge, machine-generated, not worth reviewing.
_LOCKFILES = frozenset(
    {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
        "poetry.lock", "pipfile.lock", "composer.lock", "gemfile.lock",
        "go.sum", "cargo.lock",
    }
)

# Path segments that indicate vendored / generated / build output.
_GENERATED_PATH_SEGMENTS = (
    "node_modules/",
    "/dist/", "dist/",
    "/build/", "build/",
    "vendor/",
    ".min.js", ".min.css",
    ".map",            # source maps
    "__generated__",
    ".generated.",
)


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #
@dataclass
class PreparedFile:
    """A file selected for AI review, with its (possibly truncated) patch."""

    filename: str
    status: str
    patch: str
    truncated: bool = False
    previous_filename: Optional[str] = None  # set for renames
    additions: int = 0
    deletions: int = 0


@dataclass
class SkippedFile:
    filename: str
    reason: str


@dataclass
class PreparedDiff:
    """Structured, bounded diff ready for ai_service (CLAUDE.md §5/§7)."""

    files: list[PreparedFile] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    total_files_changed: int = 0
    total_patch_chars: int = 0
    limit_reached: bool = False  # a file/size cap stopped us including everything
    truncated: bool = False      # at least one patch was shortened

    @property
    def included_count(self) -> int:
        return len(self.files)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def has_reviewable_changes(self) -> bool:
        return bool(self.files)


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #
def _extension(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def classify_skip(file: ChangedFile) -> Optional[str]:
    """Return a skip reason if the file is not worth reviewing, else None."""
    name = (file.filename or "").strip()
    if not name:
        return "missing filename"

    lowered = name.lower()
    basename = lowered.rsplit("/", 1)[-1]

    if basename in _LOCKFILES:
        return "dependency lockfile"
    if any(segment in lowered for segment in _GENERATED_PATH_SEGMENTS):
        return "generated/vendored/minified file"
    if _extension(name) in _BINARY_EXTS:
        return "binary or non-text file"
    return None


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def prepare_diff(
    files: Iterable[ChangedFile],
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_chars: int = DEFAULT_MAX_TOTAL_DIFF_CHARS,
    max_patch_chars: int = DEFAULT_MAX_PATCH_CHARS,
) -> PreparedDiff:
    """Filter, bound and structure changed files for AI analysis.

    Files are processed in the order GitHub returned them. Once a cap is hit,
    remaining files are recorded as skipped (never silently dropped).
    """
    files = list(files)
    result = PreparedDiff(total_files_changed=len(files))
    budget = max_total_chars

    for file in files:
        name = file.filename or "<unknown>"

        # 1) Irrelevant files.
        reason = classify_skip(file)
        if reason is not None:
            result.skipped.append(SkippedFile(name, reason))
            continue

        # 2) No patch supplied by GitHub (binary / too large / rename-only).
        if not file.patch:
            detail = (
                "renamed without content changes"
                if file.status == "renamed"
                else "no patch available (binary or too large)"
            )
            result.skipped.append(SkippedFile(name, detail))
            continue

        # 3) File-count cap.
        if result.included_count >= max_files:
            result.limit_reached = True
            result.skipped.append(
                SkippedFile(name, f"file limit reached (max {max_files})")
            )
            continue

        # 4) Per-file size cap.
        patch = file.patch
        truncated = False
        if len(patch) > max_patch_chars:
            patch = patch[:max_patch_chars]
            truncated = True

        # 5) Total size budget.
        if len(patch) > budget:
            if budget >= _MIN_USEFUL_PATCH_CHARS:
                patch = patch[:budget]
                truncated = True
            else:
                result.limit_reached = True
                result.skipped.append(
                    SkippedFile(name, "total diff size limit reached")
                )
                continue

        budget -= len(patch)
        result.truncated = result.truncated or truncated
        result.total_patch_chars += len(patch)
        result.files.append(
            PreparedFile(
                filename=name,
                status=file.status or "modified",
                patch=patch,
                truncated=truncated,
                previous_filename=file.previous_filename,
                additions=file.additions,
                deletions=file.deletions,
            )
        )

    return result
