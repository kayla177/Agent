import SettingsForm from "@/components/settings/SettingsForm";

export const dynamic = "force-dynamic";

async function loadPrefs() {
  try {
    const res = await fetch("http://127.0.0.1:8001/prefs", { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export default async function SettingsPage() {
  const data = await loadPrefs();
  return (
    <>
      <h1>settings</h1>
      {!data ? (
        <p className="muted">Agent service offline — start it with <code>uv run python -m web</code> (port 8001).</p>
      ) : (
        <SettingsForm prefs={data.prefs} secrets={data.secrets} />
      )}
    </>
  );
}
