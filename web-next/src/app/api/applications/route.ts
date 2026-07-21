import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { STATUSES, isStatus, today } from "@/lib/applications";

export const dynamic = "force-dynamic"; // always hit the DB, never cache

export async function GET() {
  const applications = await prisma.applications.findMany({ orderBy: { id: "asc" } });
  return NextResponse.json({ applications });
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({} as Record<string, unknown>));
  const company = String(body.company ?? "").trim();
  const role = String(body.role ?? "").trim();
  const url = String(body.url ?? "").trim();
  const notes = String(body.notes ?? "").trim();
  const status = String(body.status ?? "applied").trim() || "applied";

  if (!company || !role) {
    return NextResponse.json({ error: "Company and role are required." }, { status: 400 });
  }
  if (!isStatus(status)) {
    return NextResponse.json({ error: `status must be one of ${STATUSES.join(", ")}` }, { status: 400 });
  }
  const d = today();
  const application = await prisma.applications.create({
    data: { company, role, url, notes, status, applied_date: d, updated_date: d, auto_detected: 0 },
  });
  return NextResponse.json({ application }, { status: 201 });
}
