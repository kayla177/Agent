import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { isDocKind, nowIso } from "@/lib/resume";

export const dynamic = "force-dynamic";

// List the experience pool (text omitted; just a lightweight summary).
export async function GET() {
  const rows = await prisma.experience_docs.findMany({ orderBy: { id: "desc" } });
  const docs = rows.map((d) => ({
    id: d.id,
    filename: d.filename,
    kind: d.kind,
    chars: d.text.length,
    added_at: d.added_at,
  }));
  return NextResponse.json({ docs });
}

// Add one experience doc from pasted text or an uploaded .txt/.md file.
// PDF/DOCX parsing needs the Python parser (agents/resume_generator/parse_upload)
// and lands when the FastAPI :8001 bridge does; until then use the CLI
// (`--add-experience file.pdf`) for binary formats.
export async function POST(req: Request) {
  const body = await req.json().catch(() => ({} as Record<string, unknown>));
  const filename = String(body.filename ?? "").trim() || "pasted.md";
  const kind = String(body.kind ?? "resume").trim();
  const text = String(body.text ?? "");

  if (!text.trim()) {
    return NextResponse.json({ error: "Text is empty." }, { status: 400 });
  }
  if (!isDocKind(kind)) {
    return NextResponse.json({ error: "kind must be resume or project." }, { status: 400 });
  }

  const doc = await prisma.experience_docs.create({
    data: { filename, kind, text, added_at: nowIso() },
  });
  return NextResponse.json({ id: doc.id }, { status: 201 });
}
