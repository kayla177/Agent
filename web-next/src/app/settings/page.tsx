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
        // Two INDEPENDENT forms, each with its own save button. They are
        // separated into panels (same treatment the résumé tab uses) because a
        // flat stack made it easy to fill the profile fields, scroll past
        // "Save profile", and hit "Save settings" — which silently submits only
        // the other form.
        <div className="settings-panels">
          {profileData ? <ProfileForm profile={profileData.profile as Profile} /> : null}
          <SettingsForm prefs={data.prefs} secrets={data.secrets} />
        </div>
      )}
    </>
  );
}
