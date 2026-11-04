import { isValidGithubPrUrl } from "../utils/prUrl.js";

// Controlled input + Analyze button. Validation state is derived from the
// current value; the submit is blocked while loading or when invalid.
export default function PrInputForm({ value, onChange, onSubmit, loading }) {
  const trimmed = (value || "").trim();
  const showError = trimmed.length > 0 && !isValidGithubPrUrl(trimmed);
  const canSubmit = !loading && isValidGithubPrUrl(trimmed);

  function handleSubmit(e) {
    e.preventDefault();
    if (canSubmit) onSubmit(trimmed);
  }

  return (
    <form className="pr-form" onSubmit={handleSubmit}>
      <div className="pr-form__field">
        <input
          type="text"
          inputMode="url"
          placeholder="https://github.com/owner/repo/pull/123"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={showError ? "invalid" : ""}
          aria-label="GitHub pull request URL"
          aria-invalid={showError}
        />
        {showError ? (
          <span className="field-error">
            Enter a valid GitHub pull request URL, e.g.
            https://github.com/owner/repo/pull/123
          </span>
        ) : (
          <span className="field-hint">
            Paste a public GitHub pull request URL to analyze.
          </span>
        )}
      </div>
      <button type="submit" disabled={!canSubmit}>
        {loading ? "Analyzing…" : "Analyze"}
      </button>
    </form>
  );
}
