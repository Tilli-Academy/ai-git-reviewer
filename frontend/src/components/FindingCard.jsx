import SeverityBadge from "./SeverityBadge.jsx";

// A single AI finding: severity, category, file:line, description, explanation,
// and suggested fix. Fields that may be absent are rendered defensively.
export default function FindingCard({ finding }) {
  const {
    severity,
    category,
    file,
    line,
    description,
    explanation,
    suggestion,
  } = finding;

  const location = file
    ? line != null
      ? `${file}:${line}`
      : file
    : null;

  return (
    <article className={`finding finding--${severity}`}>
      <div className="finding__head">
        <SeverityBadge severity={severity} />
        {category && <span className="finding__category">{category}</span>}
        {location && <span className="finding__location">{location}</span>}
      </div>

      {description && <p className="finding__desc">{description}</p>}

      {explanation && (
        <div className="finding__block">
          <span className="finding__label">Why it matters</span>
          {explanation}
        </div>
      )}

      {suggestion && (
        <div className="finding__block">
          <span className="finding__label">Suggested fix</span>
          <div className="finding__suggestion">{suggestion}</div>
        </div>
      )}
    </article>
  );
}
