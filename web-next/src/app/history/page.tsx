import { prisma } from "@/lib/db";
import type { Run } from "@/lib/runs";
import HistoryTable from "@/components/history/HistoryTable";

export const dynamic = "force-dynamic";

export default async function HistoryPage() {
  const runs = (await prisma.runs.findMany({ orderBy: { id: "desc" }, take: 50 })) as Run[];
  return (
    <>
      <h1>history</h1>
      {runs.length === 0 ? (
        <p className="muted">No runs yet.</p>
      ) : (
        <HistoryTable runs={runs} />
      )}
    </>
  );
}
