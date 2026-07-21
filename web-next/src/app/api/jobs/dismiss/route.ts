import { NextResponse } from "next/server";
import { setJobStatus } from "@/lib/jobs-server";

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({} as Record<string, unknown>));
  const id = String(body.id ?? "").trim();
  if (!id) return NextResponse.json({ error: "Missing id." }, { status: 400 });
  const ok = await setJobStatus(id, "dismissed");
  if (!ok) return NextResponse.json({ error: `No job with id ${id}.` }, { status: 404 });
  return NextResponse.json({ ok: true });
}
