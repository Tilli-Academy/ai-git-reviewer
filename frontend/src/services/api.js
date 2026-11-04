// All backend communication lives here (CLAUDE.md §8). Components never call
// fetch directly. Requests go to the relative "/api" path, which Vite proxies
// to the FastAPI backend in dev (see vite.config.js).

// Build the endpoint relative to the app's base path so it works both at the
// site root and under a path-prefix proxy (e.g. /absproxy/5174/). BASE_URL
// always ends with "/", so this yields e.g. "/absproxy/5174/api/review".
const REVIEW_ENDPOINT = `${import.meta.env.BASE_URL}api/review`;

/**
 * Request an AI review for a GitHub PR URL.
 * @param {string} prUrl
 * @returns {Promise<object>} the ReviewResponse JSON
 * @throws {Error} with a user-friendly message on failure
 */
export async function reviewPullRequest(prUrl) {
  let response;
  try {
    response = await fetch(REVIEW_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pr_url: prUrl }),
    });
  } catch {
    // Network / connection failure (backend down, DNS, etc.)
    throw new Error(
      "Could not reach the server. Make sure the backend is running."
    );
  }

  let data = null;
  try {
    data = await response.json();
  } catch {
    // leave data as null; handled below
  }

  if (!response.ok) {
    throw new Error(extractErrorMessage(data, response.status));
  }
  return data;
}

// Turn a FastAPI error payload into a readable message.
// - 422 validation errors come back as { detail: [ { msg, loc }, ... ] }
// - other errors come back as { detail: "message" }
function extractErrorMessage(data, status) {
  const detail = data && data.detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && d.msg ? d.msg : null))
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  } else if (typeof detail === "string" && detail.trim()) {
    return detail;
  }

  if (status === 429) return "GitHub rate limit reached. Please try again later.";
  if (status >= 500) return `Server error (HTTP ${status}). Please try again.`;
  return `Request failed (HTTP ${status}).`;
}
