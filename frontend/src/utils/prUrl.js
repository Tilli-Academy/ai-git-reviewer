// Client-side GitHub PR URL check — mirrors the backend's regex so we can give
// instant feedback before calling the API. The backend remains the source of
// truth and validates again server-side.
const GITHUB_PR_URL_RE =
  /^https?:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+\/?$/;

export function isValidGithubPrUrl(value) {
  return GITHUB_PR_URL_RE.test((value || "").trim());
}
