import type { Stats } from "@/lib/applications";

export default function StatsRow({ stats }: { stats: Stats }) {
  const tiles = [
    { label: "total", value: String(stats.total) },
    { label: "active", value: String(stats.active) },
    { label: "interviews", value: String(stats.interviews) },
    { label: "response rate", value: `${stats.responseRate}%` },
  ];
  return (
    <div className="stat-row">
      {tiles.map((t) => (
        <div key={t.label} className="stat-tile">
          <div className="stat-value">{t.value}</div>
          <div className="stat-label">{t.label}</div>
        </div>
      ))}
    </div>
  );
}
