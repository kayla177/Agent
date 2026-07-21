import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const appId = Number(id);
  if (!Number.isInteger(appId)) {
    return NextResponse.json({ error: "Invalid id." }, { status: 400 });
  }
  try {
    await prisma.applications.delete({ where: { id: appId } });
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ error: `No application with id ${appId}.` }, { status: 404 });
  }
}
