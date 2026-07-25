"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// The primary domains live in the hub's planet pill-nav (briefing/stocks/jobs/
// tracker). The top bar carries only the utility routes.
const TABS = [
  { href: "/history", label: "history" },
  { href: "/settings", label: "settings" },
];

export default function TopNav() {
  const pathname = usePathname();
  return (
    <header className="topbar">
      <Link className="brand" href="/">daily · agents</Link>
      <nav>
        {TABS.map((t) => {
          const active = t.href === "/" ? pathname === "/" : pathname.startsWith(t.href);
          return (
            <Link key={t.href} href={t.href} className={active ? "on" : ""}>
              {t.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
