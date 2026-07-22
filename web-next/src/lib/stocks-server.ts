import { AGENT_SERVICE_URL } from "./agent-service";

export type StockDesk = {
  watchlist: { labels: string[]; series: number[]; colors: string[] };
  trend: { labels: number[]; series: { name: string; data: number[] }[] };
  signals: {
    symbol: string; price: number | null; rsi: number | null;
    signal: "buy" | "sell" | "hold"; color: string; reason: string;
  }[];
  warnings: string[];
};

// Server-only: fetch the computed stocks desk (watchlist %, price trend, signals).
// The backend caches ~5min; we cap the wait so a cold market sweep can't hang the
// page. Returns null if the service is offline → the page shows a graceful state.
export async function getStocksDesk(): Promise<StockDesk | null> {
  try {
    const res = await fetch(`${AGENT_SERVICE_URL}/stocks/desk`, {
      cache: "no-store",
      signal: AbortSignal.timeout(20000),
    });
    if (!res.ok) return null;
    return (await res.json()) as StockDesk;
  } catch {
    return null;
  }
}
