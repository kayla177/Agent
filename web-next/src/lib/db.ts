import { PrismaClient } from "@prisma/client";

// Reuse one client across dev hot-reloads (Next.js re-imports modules on change,
// which would otherwise open a new connection pool every reload).
const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma = globalForPrisma.prisma ?? new PrismaClient();

if (process.env.NODE_ENV !== "production") globalForPrisma.prisma = prisma;
