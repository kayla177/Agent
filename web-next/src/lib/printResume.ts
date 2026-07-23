// "Download PDF" without a client-side Markdown dependency or stored files:
// render the résumé Markdown to HTML on the server (POST /data/render, the same
// renderer used for agent output), open it in a print-styled window, and let the
// browser's Save-as-PDF do the export. Nothing is persisted.

function escapeHtml(s: string): string {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c] as string));
}

function printDoc(title: string, bodyHtml: string): string {
  // Self-contained light-theme print stylesheet (the app is dark; résumés print white).
  return `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>
<style>
  @page { size: letter; margin: 0.8in; }
  html, body { background: #fff; color: #111; }
  body { font: 12pt/1.5 Georgia, "Times New Roman", serif; margin: 0.8in; }
  h1, h2, h3 { font-family: Helvetica, Arial, sans-serif; line-height: 1.2; margin: 0.9em 0 0.35em; }
  h1 { font-size: 20pt; } h2 { font-size: 14pt; border-bottom: 1px solid #ccc; padding-bottom: 2px; }
  h3 { font-size: 12pt; }
  ul { margin: 0.3em 0 0.6em 1.2em; padding: 0; }
  li { margin: 0.15em 0; }
  a { color: #111; text-decoration: none; }
  p { margin: 0.35em 0; }
  @media screen { body { max-width: 8.5in; box-shadow: 0 0 0 1px #eee; } }
</style></head><body>${bodyHtml}</body></html>`;
}

export async function printResume(markdown: string, title: string): Promise<void> {
  let html: string;
  try {
    const res = await fetch("/data/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown }),
    });
    html = res.ok ? (await res.json()).html : `<pre>${escapeHtml(markdown)}</pre>`;
  } catch {
    html = `<pre>${escapeHtml(markdown)}</pre>`;
  }
  const w = window.open("", "_blank", "width=820,height=1000");
  if (!w) return; // popup blocked
  w.document.write(printDoc(title, html));
  w.document.close();
  w.focus();
  // Let the new document lay out before printing.
  setTimeout(() => w.print(), 250);
}
