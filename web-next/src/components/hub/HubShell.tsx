"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

// The four primary domains — the planet pills. Each is a real route; the hero
// stays pinned as a shared shell and re-themes to the active domain's planet.
const TABS = [
  { href: "/", label: "briefing", planet: "earth" },
  { href: "/stocks", label: "stocks", planet: "jupiter" },
  { href: "/jobs", label: "jobs", planet: "mars" },
  { href: "/tracker", label: "tracker", planet: "saturn" },
] as const;

function activeTab(pathname: string) {
  const matches = TABS.filter((t) =>
    t.href === "/" ? pathname === "/" : pathname.startsWith(t.href),
  );
  // longest matching href wins (so /tracker beats / etc.)
  return matches.sort((a, b) => b.href.length - a.href.length)[0] ?? TABS[0];
}

export default function HubShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const active = activeTab(pathname);

  useEffect(() => {
    document.body.dataset.planet = active.planet;
  }, [active.planet]);

  return (
    <>
      <div className="hero" data-planet={active.planet}>
        <div className="hero-title">agents</div>
        <div className="planet-far" />
        <div className="planet" />
        <div className="hero-sub">daily</div>
        <nav className="pill-nav">
          {TABS.map((t) => (
            <Link key={t.href} href={t.href} className={t.href === active.href ? "on" : ""}>
              {t.label}
            </Link>
          ))}
        </nav>
      </div>
      {children}
    </>
  );
}
