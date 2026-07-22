import HubShell from "@/components/hub/HubShell";

// The hub route group: the primary domains (/, /stocks, /jobs, /tracker) share the
// persistent planet hero + pill-nav. Utility routes (/history, /settings) live
// outside this group and render without the hero.
export default function HubLayout({ children }: { children: React.ReactNode }) {
  return <HubShell>{children}</HubShell>;
}
