import { STATUS_META, isStatus } from "@/lib/applications";

export default function StatusPill({ status, auto }: { status: string; auto?: boolean }) {
  const meta = isStatus(status) ? STATUS_META[status] : null;
  const color = meta?.color ?? "#8b93a6";
  return (
    <span className="status-pill" style={{ color, borderColor: color, background: `${color}1f` }}>
      {meta?.label ?? status}
      {auto ? <span title="auto-detected from Gmail">✉</span> : null}
    </span>
  );
}
