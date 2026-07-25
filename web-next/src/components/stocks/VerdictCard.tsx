import type { StockCard } from "@/lib/stocks-server";

const VERDICT_LABEL: Record<string, string> = {
  bullish: "Bullish",
  neutral: "Neutral",
  bearish: "Bearish",
};

// Score (-2..+2) as a small 5-segment meter, centre = neutral.
function ScoreMeter({ score }: { score: number | null }) {
  if (score == null) return null;
  const cells = [-2, -1, 0, 1, 2];
  return (
    <span className="vc-meter" title={`Signal lean: ${score > 0 ? "+" : ""}${score} (−2 bearish … +2 bullish)`}>
      {cells.map((c) => {
        const on = (score > 0 && c > 0 && c <= score) || (score < 0 && c < 0 && c >= score) || (c === 0);
        const tone = c > 0 ? "up" : c < 0 ? "down" : "mid";
        return <i key={c} className={`vc-seg ${tone} ${on ? "on" : ""}`} />;
      })}
    </span>
  );
}

// One stock's beginner report: verdict badge + plain summary + explained signals
// + risks / catalysts / a learn note. Informational, never advice.
export default function VerdictCard({ card }: { card: StockCard }) {
  const up = (card.pct ?? 0) >= 0;
  return (
    <article className={`verdict-card v-${card.verdict}`}>
      <header className="vc-top">
        <div className="vc-id">
          <span className="vc-sym">{card.symbol}</span>
          {card.price != null && (
            <span className="vc-price">
              ${card.price.toFixed(2)}
              {card.pct != null && (
                <em className={up ? "up" : "down"}>
                  {" "}{up ? "+" : ""}{card.pct.toFixed(1)}%
                </em>
              )}
            </span>
          )}
        </div>
        <div className="vc-verdict">
          <span className={`verdict-badge ${card.verdict}`}>{VERDICT_LABEL[card.verdict] ?? "Neutral"}</span>
          <ScoreMeter score={card.score} />
        </div>
      </header>

      {card.summary && <p className="vc-summary">{card.summary}</p>}

      {card.signals.length > 0 && (
        <details className="vc-details">
          <summary>What the signals mean</summary>
          <ul className="vc-sig-list">
            {card.signals.map((s) => (
              <li key={s.label}>
                <span className="vc-sig-head">
                  <b>{s.label}</b>
                  <span className="vc-sig-val">{s.value}</span>
                </span>
                <p>{s.meaning}</p>
              </li>
            ))}
          </ul>
        </details>
      )}

      {(card.risks.length > 0 || card.catalysts.length > 0) && (
        <div className="vc-rc">
          {card.risks.length > 0 && (
            <div className="vc-col">
              <h4>⚠️ Watch out for</h4>
              <ul>{card.risks.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          )}
          {card.catalysts.length > 0 && (
            <div className="vc-col">
              <h4>✨ Could help</h4>
              <ul>{card.catalysts.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </div>
          )}
        </div>
      )}

      {card.learn && (
        <details className="vc-details vc-learn">
          <summary>📚 Learn this</summary>
          <p>{card.learn}</p>
        </details>
      )}
    </article>
  );
}
