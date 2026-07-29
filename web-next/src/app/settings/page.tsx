import ProfileForm, { type Profile } from "@/components/settings/ProfileForm";
import SettingsForm from "@/components/settings/SettingsForm";
import { AGENT_SERVICE_URL } from "@/lib/agent-service";

export const dynamic = "force-dynamic";

async function loadJson(path: string) {
  try {
    const res = await fetch(`${AGENT_SERVICE_URL}${path}`, { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export default async function SettingsPage() {
  const [data, profileData] = await Promise.all([loadJson("/prefs"), loadJson("/data/profile")]);
  return (
    <>
      <h1>settings</h1>
      {!data ? (
        <p className="muted">Agent service offline — start it with <code>python -m server</code> (port 8001).</p>
      ) : (
        <>
          {profileData ? <ProfileForm profile={profileData.profile as Profile} /> : null}
          <SettingsForm prefs={data.prefs} secrets={data.secrets} />
        </>
      )}
    </>
  );
}
