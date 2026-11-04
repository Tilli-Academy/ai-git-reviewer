import { useState } from "react";
import PrInputForm from "../components/PrInputForm.jsx";
import Loader from "../components/Loader.jsx";
import ErrorMessage from "../components/ErrorMessage.jsx";
import SummaryPanel from "../components/SummaryPanel.jsx";
import FindingsList from "../components/FindingsList.jsx";
import { reviewPullRequest } from "../services/api.js";

// Orchestrates the review workflow and the loading / error / result states.
export default function DashboardPage() {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  async function handleAnalyze(prUrl) {
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await reviewPullRequest(prUrl);
      setResult(data);
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  const hasFindings = result && result.findings && result.findings.length > 0;

  return (
    <main className="container">
      <PrInputForm
        value={url}
        onChange={setUrl}
        onSubmit={handleAnalyze}
        loading={loading}
      />

      {loading && <Loader />}
      {!loading && error && <ErrorMessage message={error} />}

      {!loading && !error && result && (
        <>
          <SummaryPanel result={result} />
          {hasFindings ? (
            <FindingsList findings={result.findings} />
          ) : (
            <div className="empty-state">
              No potential issues were detected by the AI review. This does not
              guarantee the code is bug-free — always have a human review it.
            </div>
          )}
          <p className="disclaimer">
            AI-generated suggestions. Verify every finding before acting on it.
          </p>
        </>
      )}
    </main>
  );
}
