# Plan 2 — Next.js Scaffold + Prisma Introspection

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stand up a Next.js (TypeScript, App Router) app in `web-next/` that reads the shared SQLite DB through Prisma (introspected, not migrated), renders the space/planets nav + theme, and proves the full stack with a home page that lists the migrated applications.

**Architecture:** `web-next/` at repo root. Prisma introspects `data/control_center.db` (Python-owned schema from Plan 1) via `prisma db pull` — never `prisma migrate`. Design tokens are ported from `web/static/app.css` as plain CSS custom properties in `globals.css` (version-agnostic; Tailwind is used only for layout utilities). Server components query Prisma directly.

**Tech Stack:** Next.js 15 (App Router), TypeScript, Tailwind (as scaffolded), Prisma + @prisma/client, Node 22 / npm 10.

## Global Constraints

- **Prisma introspects; never migrates.** Use `prisma db pull` + `prisma generate` only. The Python `CREATE TABLE IF NOT EXISTS` DDL in `store_db.py`/`web/schema.sql` is the schema source of truth. Never run `prisma migrate`.
- **SQLite `file:` path is relative to `prisma/schema.prisma`.** From `web-next/prisma/`, the repo-root DB is `file:../../data/control_center.db`. (Prisma docs: `file:./dev.db` → `prisma/dev.db`.) If the runtime cannot find the DB but the CLI can, fall back to an absolute path in the gitignored `web-next/.env` `DATABASE_URL` and switch the datasource to `env("DATABASE_URL")`.
- **Do not modify the Python app, the DB schema, or `data/control_center.db`.** This plan is additive: everything lives under `web-next/`, except one root `.gitignore` addition.
- **Prisma model/accessor names come from introspection** — they match the SQLite table names (`applications`, `jobs`, `runs`, `node_events`). After `db pull`, open the generated `web-next/prisma/schema.prisma` and use the exact model names it produced.
- **Palette (port verbatim from `web/static/app.css`):** bg `#000`; text `#eef1f6`; muted `#8b93a6`; panel `rgba(255,255,255,0.04)`; border `rgba(255,255,255,0.12)`; radius `18px`; display serif `"Italiana"`. Per-planet accents: earth `#4a90ff`, jupiter `#e6b063`, mars `#e07a4a`, saturn `#ddca9c`. Status: red `#e0705a`, amber `#e0b15a`, green `#7fc08a`. Nav tabs (order): dashboard, jobs, applications, history, settings — lowercase, letter-spaced serif, active tab underlined in `--accent`.

---

## File structure (Plan 2)

- Create: `web-next/` (via create-next-app) — app scaffold.
- Create: `web-next/prisma/schema.prisma` — datasource + generator + introspected models.
- Create: `web-next/src/lib/db.ts` — Prisma client singleton.
- Modify: `web-next/src/app/globals.css` — ported design tokens, starfield, nav/topbar, container, headings.
- Create/Modify: `web-next/src/app/layout.tsx` — root layout: Italiana font link, `<body data-planet="earth">`, `<TopNav/>`, container.
- Create: `web-next/src/components/TopNav.tsx` — nav bar (client component, active tab via `usePathname`).
- Modify: `web-next/src/app/page.tsx` — home page: server component reading `prisma.applications` (proves the stack).
- Create: `web-next/next.config.ts` proxy rewrites to FastAPI `:8001` (inert until Plan 5).
- Modify: root `.gitignore` — ignore `web-next/node_modules`, `web-next/.next`.

---

## Task 1: Scaffold the Next.js app

**Files:**
- Create: `web-next/**` (scaffold)
- Modify: `.gitignore` (root)

**Interfaces:**
- Produces: a buildable Next.js app at `web-next/` with `src/`, App Router, TypeScript, Tailwind, import alias `@/*`.

- [ ] **Step 1: Scaffold non-interactively**

Run from repo root (`/Users/kayla.li/.superset/Agent`):

```bash
npx --yes create-next-app@latest web-next \
  --ts --tailwind --eslint --app --src-dir \
  --import-alias "@/*" --use-npm --turbopack --yes
```

This must complete without interactive prompts. If create-next-app still prompts for an unspecified option, re-run adding the corresponding flag (e.g. `--no-turbopack`). Do not answer prompts interactively — the flags must cover them.

- [ ] **Step 2: Verify it builds**

Run: `cd web-next && npm run build`
Expected: build completes, exit 0, "Compiled successfully" (a default create-next-app builds clean).

- [ ] **Step 3: Add root .gitignore entries**

Append to the repo-root `.gitignore`:

```
# Next.js app (web-next) build + deps
web-next/node_modules/
web-next/.next/
web-next/.env
```

(create-next-app also writes its own `web-next/.gitignore`; these root entries are belt-and-suspenders and keep `web-next/.env` — used for the optional absolute-path fallback — out of git.)

- [ ] **Step 4: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next .gitignore
git commit -m "feat(web-next): scaffold Next.js app (TS, App Router, Tailwind)"
```

Note: confirm `git status` shows `web-next/node_modules` is NOT staged (the .gitignore must exclude it). If node_modules appears staged, fix .gitignore before committing.

---

## Task 2: Prisma setup + introspection

**Files:**
- Create: `web-next/prisma/schema.prisma`
- Modify: `web-next/package.json` (deps added by npm)

**Interfaces:**
- Consumes: `data/control_center.db` (Plan 1 tables: applications, jobs, runs, node_events).
- Produces: generated `@prisma/client` with models for all four tables; the exact model names are recorded for downstream tasks.

- [ ] **Step 1: Install Prisma**

Run: `cd web-next && npm install prisma --save-dev && npm install @prisma/client`

- [ ] **Step 2: Create the schema with datasource + generator**

Create `web-next/prisma/schema.prisma`:

```prisma
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "sqlite"
  url      = "file:../../data/control_center.db"
}
```

- [ ] **Step 3: Introspect the existing DB**

Run: `cd web-next && npx prisma db pull`
Expected: "Introspecting based on datasource..." then a success line reporting **4 models** written to `prisma/schema.prisma` (applications, jobs, runs, node_events). `sqlite_sequence` is skipped by Prisma (internal).

If it reports "0 models" or "could not find database": the `file:` path depth is wrong. Confirm `../../data/control_center.db` resolves from `web-next/prisma/` to the repo-root `data/` dir; adjust `../` count and re-run. Do NOT create a new DB.

- [ ] **Step 4: Record the model names + generate the client**

Open `web-next/prisma/schema.prisma` and note the exact `model` names introspection produced (expected: `applications`, `jobs`, `runs`, `node_events`). Write them into the report — downstream tasks (and Step of Task 5) use these accessor names (`prisma.applications`, etc.).

Run: `cd web-next && npx prisma generate`
Expected: "Generated Prisma Client" success.

- [ ] **Step 5: Verify a real query against the migrated data**

Run:
```bash
cd web-next && node -e "const{PrismaClient}=require('@prisma/client');const p=new PrismaClient();p.applications.count().then(n=>{console.log('applications:',n);return p.\$disconnect()})"
```
Expected: `applications: 2` (the Stripe + Figma rows migrated in Plan 1). If the model accessor differs from `applications`, use the name from Step 4.

- [ ] **Step 6: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/prisma/schema.prisma web-next/package.json web-next/package-lock.json
git commit -m "feat(web-next): introspect shared SQLite via Prisma (db pull)"
```

Note: the generated client lives under `web-next/node_modules/.prisma` (gitignored) — regenerated via `prisma generate`. Do not commit it.

---

## Task 3: Prisma client singleton

**Files:**
- Create: `web-next/src/lib/db.ts`

**Interfaces:**
- Produces: `import { prisma } from "@/lib/db"` — a single shared `PrismaClient` (guards against dev hot-reload creating many clients).

- [ ] **Step 1: Write `web-next/src/lib/db.ts`**

```ts
import { PrismaClient } from "@prisma/client";

// Reuse one client across dev hot-reloads (Next.js re-imports modules on change,
// which would otherwise open a new connection pool every reload).
const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma = globalForPrisma.prisma ?? new PrismaClient();

if (process.env.NODE_ENV !== "production") globalForPrisma.prisma = prisma;
```

- [ ] **Step 2: Type-check**

Run: `cd web-next && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/lib/db.ts
git commit -m "feat(web-next): Prisma client singleton"
```

---

## Task 4: Design tokens + global CSS (space/planets theme)

**Files:**
- Modify: `web-next/src/app/globals.css`

**Interfaces:**
- Produces: CSS custom properties (`--bg`, `--text`, `--muted`, `--panel`, `--border`, `--accent`, `--accent-soft`, `--glow`, `--red`, `--amber`, `--green`, `--radius`, `--serif`, `--sans`), per-planet accent overrides via `body[data-planet=...]`, the starfield body background, and `.topbar`/`.brand`/`nav`/`.container`/`h1`/`h2` styles. Ported from `web/static/app.css`.

- [ ] **Step 1: Replace the token/base section of `globals.css`**

Keep the Tailwind directives that create-next-app placed at the very top of the file (e.g. `@import "tailwindcss";` for v4, or the `@tailwind base/components/utilities` lines for v3 — leave whatever is there untouched at the top). Below them, replace the rest of the file with:

```css
:root {
  --bg: #000000;
  --panel: rgba(255, 255, 255, 0.04);
  --panel-2: rgba(255, 255, 255, 0.06);
  --border: rgba(255, 255, 255, 0.12);
  --border-soft: rgba(255, 255, 255, 0.08);
  --text: #eef1f6;
  --muted: #8b93a6;

  --accent: #4a90ff;
  --accent-soft: rgba(74, 144, 255, 0.9);
  --glow: rgba(30, 90, 236, 0.55);
  --red: #e0705a;
  --amber: #e0b15a;
  --green: #7fc08a;

  --radius: 18px;
  --serif: "Italiana", "Cormorant Garamond", Didot, Georgia, serif;
  --sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

body[data-planet="earth"]   { --accent:#4a90ff; --accent-soft:rgba(74,144,255,.95); --glow:rgba(30,90,236,.55); }
body[data-planet="jupiter"] { --accent:#e6b063; --accent-soft:rgba(230,176,99,.95); --glow:rgba(200,140,70,.5); }
body[data-planet="mars"]    { --accent:#e07a4a; --accent-soft:rgba(224,122,74,.95); --glow:rgba(200,80,40,.5); }
body[data-planet="saturn"]  { --accent:#ddca9c; --accent-soft:rgba(221,202,156,.95); --glow:rgba(190,160,110,.45); }

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }

body {
  margin: 0;
  min-height: 100vh;
  color: var(--text);
  font: 15px/1.6 var(--sans);
  background-color: #000;
  background-image:
    radial-gradient(1px 1px at 20% 30%, rgba(255,255,255,.7), transparent),
    radial-gradient(1px 1px at 75% 15%, rgba(255,255,255,.5), transparent),
    radial-gradient(1.5px 1.5px at 50% 60%, rgba(255,255,255,.6), transparent),
    radial-gradient(1px 1px at 12% 78%, rgba(255,255,255,.45), transparent),
    radial-gradient(1px 1px at 88% 68%, rgba(255,255,255,.5), transparent),
    radial-gradient(1px 1px at 35% 88%, rgba(255,255,255,.4), transparent),
    radial-gradient(1200px 700px at 50% -10%, rgba(30,60,140,.18), transparent 70%);
  background-attachment: fixed;
  background-repeat: no-repeat;
}

a { color: var(--accent); text-decoration: none; }
a:hover { color: #fff; }

.topbar {
  display: flex; align-items: center; gap: 1.5rem;
  padding: 1rem 1.75rem;
  border-bottom: 1px solid var(--border-soft);
  position: sticky; top: 0; z-index: 20;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.brand {
  font-family: var(--serif); font-size: 1.4rem; letter-spacing: 6px;
  color: #fff; text-transform: lowercase;
}
.topbar nav { display: flex; gap: 1.5rem; margin-left: auto; }
.topbar nav a {
  color: var(--muted); font-family: var(--serif); font-size: 1rem;
  letter-spacing: 2px; text-transform: lowercase;
  padding-bottom: 2px; border-bottom: 1px solid transparent;
}
.topbar nav a:hover { color: var(--text); }
.topbar nav a.on { color: #fff; border-bottom-color: var(--accent); }

.container { max-width: 1040px; margin: 2.25rem auto; padding: 0 1.5rem; }

h1 { font-family: var(--serif); font-weight: 400; font-size: 2rem; letter-spacing: 2px; margin: 0 0 1.25rem; }
h2 {
  font-family: var(--serif); font-weight: 400; font-size: 1rem; letter-spacing: 5px;
  text-transform: lowercase; color: var(--muted); margin: 2.25rem 0 1rem;
}
.muted { color: var(--muted); }
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/app/globals.css
git commit -m "feat(web-next): port space/planets design tokens + starfield to globals.css"
```

---

## Task 5: TopNav + root layout

**Files:**
- Create: `web-next/src/components/TopNav.tsx`
- Modify: `web-next/src/app/layout.tsx`

**Interfaces:**
- Consumes: globals.css classes (Task 4).
- Produces: `<TopNav/>` rendering the 5 nav links with active-tab underline; a root layout that loads the Italiana font, sets `<body data-planet="earth">`, and wraps children in `<main class="container">`.

- [ ] **Step 1: Write `web-next/src/components/TopNav.tsx`**

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/", label: "dashboard" },
  { href: "/jobs", label: "jobs" },
  { href: "/applications", label: "applications" },
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
```

- [ ] **Step 2: Rewrite `web-next/src/app/layout.tsx`**

```tsx
import type { Metadata } from "next";
import "./globals.css";
import TopNav from "@/components/TopNav";

export const metadata: Metadata = {
  title: "daily · agents",
  description: "Personal agent control center",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Italiana&display=swap"
          rel="stylesheet"
        />
      </head>
      <body data-planet="earth">
        <TopNav />
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
```

Note: if create-next-app added `next/font` (Geist) imports and body className in the generated `layout.tsx`, remove them — the theme uses the Google-hosted Italiana + the system sans stack from globals.css, and a leftover font className on `<body>` would override it.

- [ ] **Step 3: Type-check**

Run: `cd web-next && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/components/TopNav.tsx web-next/src/app/layout.tsx
git commit -m "feat(web-next): TopNav + root layout with planet theme"
```

---

## Task 6: Home page proving the stack + proxy config

**Files:**
- Modify: `web-next/src/app/page.tsx`
- Create: `web-next/next.config.ts` (or modify the scaffolded one)

**Interfaces:**
- Consumes: `prisma` (Task 3), the introspected `applications` model (Task 2).
- Produces: `/` rendering the migrated applications from SQLite — the end-to-end proof for this plan.

- [ ] **Step 1: Rewrite `web-next/src/app/page.tsx` as a server component**

Use the exact model accessor recorded in Task 2 Step 4 (expected `prisma.applications`):

```tsx
import { prisma } from "@/lib/db";

export const dynamic = "force-dynamic"; // always read live DB, no build-time cache

export default async function Home() {
  const apps = await prisma.applications.findMany({ orderBy: { id: "asc" } });
  return (
    <>
      <h1>dashboard</h1>
      <p className="muted">
        Next.js + Prisma reading the shared SQLite control center.
      </p>
      <h2>applications ({apps.length})</h2>
      {apps.length === 0 ? (
        <p className="muted">No applications logged yet.</p>
      ) : (
        <ul>
          {apps.map((a) => (
            <li key={a.id}>
              <strong>{a.company}</strong> — {a.role}{" "}
              <span style={{ color: "var(--muted)" }}>({a.status})</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
```

If `npx tsc --noEmit` flags that a column field name differs (e.g. introspection produced `applied_date` vs `appliedDate`), use the exact field names from the generated `schema.prisma`. Only `company`, `role`, `status`, `id` are referenced here — all are simple lowercase columns, so no casing surprise is expected.

- [ ] **Step 2: Add the FastAPI proxy rewrites (inert until Plan 5)**

Write `web-next/next.config.ts`:

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Proxy agent-run + SSE endpoints to the (future) slim FastAPI on :8001.
  // Inert until Plan 5 moves FastAPI to :8001; harmless before then.
  async rewrites() {
    return [
      { source: "/agents/:path*", destination: "http://127.0.0.1:8001/agents/:path*" },
      { source: "/runs/:path*", destination: "http://127.0.0.1:8001/runs/:path*" },
    ];
  },
};

export default nextConfig;
```

If create-next-app scaffolded `next.config.mjs` or `next.config.js` instead, replace that file's contents with the equivalent (keep the file extension that exists; convert `import type`/`export default` to CJS if it's `.js`).

- [ ] **Step 3: Build**

Run: `cd web-next && npm run build`
Expected: build succeeds, exit 0.

- [ ] **Step 4: Verify end-to-end at runtime**

```bash
cd web-next && npm run dev > /tmp/next-dev.log 2>&1 &
sleep 8
curl -s localhost:3000 | grep -c "Stripe"   # expect >= 1
curl -s localhost:3000 | grep -c "Figma"     # expect >= 1
curl -s -o /dev/null -w "http %{http_code}\n" localhost:3000
# stop the dev server
kill %1 2>/dev/null
```
Expected: Stripe and Figma each match ≥ 1, http 200. (:3000 must be free; if taken, run `npm run dev -- -p 3001` and curl 3001.)

- [ ] **Step 5: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/app/page.tsx web-next/next.config.ts
git commit -m "feat(web-next): home page reads applications from SQLite; FastAPI proxy config"
```

---

## Plan 2 verification summary

- `cd web-next && npm run build` → succeeds
- `npx prisma db pull` → 4 models (applications, jobs, runs, node_events)
- `node -e "...applications.count()"` → 2
- `npm run dev` → `/` returns 200 and lists Stripe + Figma from SQLite
- Nav renders 5 tabs; active tab underlined; starfield + Italiana theme visible
- `git status` clean; `web-next/node_modules` and `web-next/.next` untracked (gitignored)

## Notes / carry-forward to Plan 3

- The introspected model + field names (recorded in Task 2) are the contract for Plan 3's CRUD API routes.
- FastAPI is still on :8000 (Jinja app intact); the proxy rewrites target :8001 and stay inert until Plan 5's slim-down.
- The two apps run independently for now: Python/Jinja on :8000, Next.js on :3000, both reading the same SQLite file.

## Self-review notes
- **Spec coverage:** scaffold (T1), Prisma introspection + client (T2, T3), theme (T4), nav+layout (T5), stack-proof home + proxy (T6) — all roadmap items for Plan 2 covered.
- **Introspection-not-migration** enforced in Global Constraints and Task 2 (only `db pull`/`generate`).
- **Path footgun** addressed with the exact `../../` depth (Context7-confirmed) + a documented absolute-path fallback.
- **Model-name uncertainty** handled by recording introspected names in T2 and referencing them in T6, with a tsc gate.
