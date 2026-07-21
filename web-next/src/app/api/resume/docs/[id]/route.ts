import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";

// Next 15/16: dynamic route params are async and must be awaited.
export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const docId = Number(id);
  if (!Number.isInteger(docId)) {
    return NextResponse.json({ error: "Invalid id." }, { status: 400 });
  }
  try {
    await prisma.experience_docs.delete({ where: { id: docId } });
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ error: `No experience doc with id ${docId}.` }, { status: 404 });
  }
}
