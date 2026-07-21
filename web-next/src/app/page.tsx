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
