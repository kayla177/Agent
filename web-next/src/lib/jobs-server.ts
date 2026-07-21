import { prisma } from "@/lib/db";
import { today } from "@/lib/applications";

// Update a job's status on BOTH the mirrored `status` column AND the `data` JSON
// blob (the Python store's read source of truth), so the two views never drift.
// Returns false if no row with that id exists.
export async function setJobStatus(id: string, status: string): Promise<boolean> {
  const rec = await prisma.jobs.findUnique({ where: { id } });
  if (!rec) return false;
  let blob: Record<string, unknown> = {};
  try { blob = JSON.parse(rec.data || "{}"); } catch { blob = {}; }
  const d = today();
  blob.id = id;
  blob.status = status;
  blob.last_seen = d;
  await prisma.jobs.update({
    where: { id },
    data: { status, last_seen: d, data: JSON.stringify(blob) },
  });
  return true;
}
