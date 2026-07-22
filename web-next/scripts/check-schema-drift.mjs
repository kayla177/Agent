#!/usr/bin/env node
// Drift guard: the root schema.sql is the single source of truth for the DB.
// This builds a throwaway SQLite DB from schema.sql and diffs it against the
// Prisma datamodel (prisma/schema.prisma). Non-zero exit = they disagree —
// regenerate the mirror with:  npx prisma db pull   (after pointing at a DB
// freshly built from schema.sql), or fix schema.sql.
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const run = (args) =>
  execFileSync("npx", args, { cwd: process.cwd(), stdio: ["ignore", "pipe", "pipe"] }).toString();

const dir = mkdtempSync(join(tmpdir(), "schema-drift-"));
const dbUrl = `file:${join(dir, "check.db")}`;
try {
  run(["prisma", "db", "execute", "--url", dbUrl, "--file", "../schema.sql"]);
  try {
    run(["prisma", "migrate", "diff",
         "--from-schema-datamodel", "prisma/schema.prisma",
         "--to-url", dbUrl, "--exit-code"]);
    console.log("✓ schema.prisma matches schema.sql — no drift.");
  } catch (e) {
    console.error("✗ DRIFT: prisma/schema.prisma is out of sync with schema.sql.\n");
    console.error((e.stdout?.toString() || "") + (e.stderr?.toString() || ""));
    console.error("Fix schema.sql, or regenerate the mirror from it (prisma db pull).");
    process.exit(1);
  }
} finally {
  rmSync(dir, { recursive: true, force: true });
}
