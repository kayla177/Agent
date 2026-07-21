import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { isResumeStatus, nowIso } from "@/lib/resume";

export const dynamic = "force-dynamic";

// job_id can contain ":" and "/" (e.g. Workday ids), so it travels as a query
// param / body field rather than a path segment (which can't hold slashes).
//   GET /api/resumes            -> list all resumes
//   GET /api/resumes?jobId=<id> -> one resume
export async function GET(req: Request) {
  const jobId = new URL(req.url).searchParams.get("jobId");
  if (jobId) {
    const resume = await prisma.resumes.findUnique({ where: { job_id: jobId } });
    if (!resume) {
      return NextResponse.json({ error: "No resume for that job." }, { status: 404 });
    }
    return NextResponse.json({ resume });
  }
  const resumes = await prisma.resumes.findMany({ orderBy: { updated_at: "desc" } });
  return NextResponse.json({ resumes });
}

// Edit a generated resume: save markdown and/or flip draft<->final.
//   PATCH /api/resumes  body: { jobId, markdown?, status? }
export async function PATCH(req: Request) {
  const body = await req.json().catch(() => ({} as Record<string, unknown>));
  const jobId = String(body.jobId ?? "").trim();
  if (!jobId) {
    return NextResponse.json({ error: "jobId is required." }, { status: 400 });
  }

  const data: { markdown?: string; status?: string; updated_at: string } = {
    updated_at: nowIso(),
  };
  if (typeof body.markdown === "string") data.markdown = body.markdown;
  if (body.status !== undefined) {
    const status = String(body.status).trim();
    if (!isResumeStatus(status)) {
      return NextResponse.json({ error: "status must be draft or final." }, { status: 400 });
    }
    data.status = status;
  }
  if (data.markdown === undefined && data.status === undefined) {
    return NextResponse.json({ error: "Nothing to update." }, { status: 400 });
  }

  try {
    const resume = await prisma.resumes.update({ where: { job_id: jobId }, data });
    return NextResponse.json({ resume });
  } catch {
    return NextResponse.json({ error: `No resume for job ${jobId}.` }, { status: 404 });
  }
}
