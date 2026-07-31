import { AGENT_SERVICE_URL } from "./agent-service";

export type StockVerdict = "bullish" | "neutral" | "bearish";

export type ExplainedSignal = { label: string; value: string; meaning: string };

export type StockCard = {
  symbol: string;
  price: number | null;
  pct: number | null;
  verdict: StockVerdict;
  score: number | null; // -2..+2
  signal: string; // buy | sell | hold (transparent heuristic)
  reason: string;
  rsi: number | null;
  summary: string; // plain-English take
  signals: ExplainedSignal[]; // explained indicators
  risks: string[];
  catalysts: string[];
  learn: string;
};

export type IndexTile = {
  symbol: string;
  name: string;
  price: number | null;
  pct: number | null;
  series: number[];
};

export type StockDesk = {
  generatedAt: string | null; // ISO run timestamp; null when live/deterministic
  live: boolean; // true = model-free fallback (no saved analysis yet)
  market: {
    indices: IndexTile[];
    movers: { symbol: string; pct: number }[];
    read: string;
  } | null;
  cards: StockCard[];
  trend: { labels: number[]; series: { name: string; data: number[] }[] };
  warnings: string[];
};

// Server-only: fetch the computed stocks desk (overview, verdict cards, trend).
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
