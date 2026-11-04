import SeverityBadge from "./SeverityBadge.jsx";

const SEVERITY_ORDER = ["critical", "high", "medium", "low"];

// PR metadata + repository info + AI summary + total findings + severity counts.
export default function SummaryPanel({ result }) {
  const { repository, pull_request, summary, total_findings, findings_by_severity } =
    result;
  const counts = findings_by_severity || {};

  return (
    <section className="card summary">
      <h2 className="summary__title">
        {pull_request?.title || "Pull request"}
      </h2>

      <div className="summary__meta">
        <span>
          <strong>{repository?.full_name}</strong>
        </span>
        {pull_request?.url ? (
          <a href={pull_request.url} target="_blank" rel="noreferrer">
            PR #{pull_request.number}
          </a>
        ) : (
          <span>PR #{pull_request?.number}</span>
        )}
        {pull_request?.author && <span>by {pull_request.author}</span>}
        {pull_request?.state && <span>state: {pull_request.state}</span>}
      </div>

      <div>
        <span className="finding__label">AI summary</span>
        <div className="summary__text">{summary}</div>
      </div>

      <div className="summary__counts">
        <span className="summary__total">
          {total_findings} finding{total_findings === 1 ? "" : "s"}
        </span>
        {SEVERITY_ORDER.map((sev) =>
          counts[sev] ? (
            <SeverityBadge key={sev} severity={sev} count={counts[sev]} />
          ) : null
        )}
      </div>
    </section>
  );
}
