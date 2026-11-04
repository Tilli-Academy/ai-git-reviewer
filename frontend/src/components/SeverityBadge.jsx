const KNOWN = ["critical", "high", "medium", "low"];

// A small colored pill for a severity level, optionally with a count.
export default function SeverityBadge({ severity, count }) {
  const key = KNOWN.includes(severity) ? severity : "muted";
  return (
    <span className={`badge badge--${key}`}>
      {severity}
      {typeof count === "number" ? ` ${count}` : ""}
    </span>
  );
}
