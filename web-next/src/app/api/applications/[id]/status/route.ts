import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { STATUSES, isStatus, today } from "@/lib/applications";

// Next 15/16: dynamic route params are async and must be awaited.
export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const appId = Number(id);
  if (!Number.isInteger(appId)) {
    return NextResponse.json({ error: "Invalid id." }, { status: 400 });
  }
  const body = await req.json().catch(() => ({} as Record<string, unknown>));
  const status = String(body.status ?? "").trim();
  const autoDetected = body.auto_detected ? 1 : 0; // manual update (default) clears the flag
  if (!isStatus(status)) {
    return NextResponse.json({ error: `status must be one of ${STATUSES.join(", ")}` }, { status: 400 });
  }
  try {
    const application = await prisma.applications.update({
      where: { id: appId },
      data: { status, updated_date: today(), auto_detected: autoDetected },
    });
    return NextResponse.json({ application });
  } catch {
    return NextResponse.json({ error: `No application with id ${appId}.` }, { status: 404 });
  }
}
