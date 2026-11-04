import FindingCard from "./FindingCard.jsx";
import SeverityBadge from "./SeverityBadge.jsx";

const SEVERITY_ORDER = ["critical", "high", "medium", "low"];

// Groups findings by severity (critical -> low) and renders each group.
export default function FindingsList({ findings }) {
  const groups = SEVERITY_ORDER.map((sev) => ({
    severity: sev,
    items: findings.filter((f) => f.severity === sev),
  })).filter((g) => g.items.length > 0);

  // Any findings with an unknown/other severity still get shown.
  const others = findings.filter((f) => !SEVERITY_ORDER.includes(f.severity));
  if (others.length) groups.push({ severity: "other", items: others });

  return (
    <div>
      {groups.map((group) => (
        <section className="findings-group" key={group.severity}>
          <h3 className="findings-group__heading">
            <SeverityBadge severity={group.severity} count={group.items.length} />
          </h3>
          {group.items.map((finding, i) => (
            <FindingCard key={`${group.severity}-${i}`} finding={finding} />
          ))}
        </section>
      ))}
    </div>
  );
}
