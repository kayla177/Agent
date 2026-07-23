// Beginner glossary — static definitions of the terms used on this page. No
// backend; it's here so a new investor can build intuition while reading cards.
const TERMS: { term: string; def: string }[] = [
  {
    term: "Bullish / Bearish",
    def: "Bullish means signals lean positive (price may rise); bearish means they lean negative. Neutral is no clear lean.",
  },
  {
    term: "RSI (Relative Strength Index)",
    def: "A 0–100 momentum gauge. Above ~70 is 'overbought' (risen fast, may pause); below ~30 is 'oversold' (fallen hard, may bounce).",
  },
  {
    term: "Moving average (SMA20 / SMA50)",
    def: "The average price over the last 20 or 50 days. Price above both is a short-term uptrend; below both is a downtrend.",
  },
  {
    term: "MACD",
    def: "Compares fast vs slow momentum. Positive means upward momentum is building; negative means it's fading.",
  },
  {
    term: "52-week high",
    def: "The highest price over the past year. 'Off its high' tells you how far the stock has pulled back from its peak.",
  },
  {
    term: "Sentiment",
    def: "Whether recent news coverage skews positive or negative — a read on the mood around a stock, not its fundamentals.",
  },
];

export default function StockGlossary() {
  return (
    <details className="glossary">
      <summary>New to this? Key terms explained</summary>
      <dl className="glossary-list">
        {TERMS.map((t) => (
          <div key={t.term}>
            <dt>{t.term}</dt>
            <dd>{t.def}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
