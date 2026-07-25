// Compile a LaTeX résumé to PDF via the backend (Tectonic) and download it.
// Returns { ok:false, error, log } on a LaTeX error so the caller can fall back
// to the browser print path (option 3). Requires the agent service on :8001.

export type PdfResult = { ok: true } | { ok: false; error: string; log?: string };

export async function downloadPdfFromTex(tex: string, filename: string): Promise<PdfResult> {
  let res: Response;
  try {
    res = await fetch("/data/resume/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tex, filename }),
    });
  } catch {
    return { ok: false, error: "Could not reach the agent service (is FastAPI on :8001 running?)." };
  }
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    return { ok: false, error: j.error ?? "LaTeX compile failed.", log: j.log };
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".pdf") ? filename : `${filename}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return { ok: true };
}
