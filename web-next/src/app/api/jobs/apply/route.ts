import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { setJobStatus } from "@/lib/jobs-server";
import { today } from "@/lib/applications";

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({} as Record<string, unknown>));
  const id = String(body.id ?? "").trim();
  if (!id) return NextResponse.json({ error: "Missing id." }, { status: 400 });

  const rec = await prisma.jobs.findUnique({ where: { id } });
  if (!rec) return NextResponse.json({ error: `No job with id ${id}.` }, { status: 404 });

  const d = today();
  // Mirror web/routers/jobs.py: hand the role to the tracker, then mark applied.
  await prisma.applications.create({
    data: {
      company: rec.company, role: rec.title, url: rec.url, status: "applied",
      applied_date: d, updated_date: d, notes: "", auto_detected: 0,
    },
  });
  await setJobStatus(id, "applied");
  return NextResponse.json({ ok: true });
}
