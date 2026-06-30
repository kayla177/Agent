"""Headlines node — Google News RSS per topic, summarized by the local model.

RSS is free and needs no key (same technique as the morning-briefing news node).
Headlines are passed to the local LLM (cheap, private) for a tight 1-2 sentence
summary of the key market theme. The LLM only summarizes text — it never
produces or touches any price figures.
"""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET

import httpx

from agents.stock_digest.state import StockDigestState
from agents.stock_digest.watchlist import get_headline_topics, get_headlines_per_topic
from shell.model_router import llm

_RSS_URL = "https://news.google.com/rss/search"


def _fetch_headlines(topic: str, limit: int) -> list[str]:
    query = urllib.parse.quote(topic)
    url = f"{_RSS_URL}?q={query}&hl=en-US&gl=US&ceid=US:en"
    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    titles = [item.findtext("title", "").strip() for item in root.iter("item")]
    return [t for t in titles if t][:limit]


def fetch_headlines() -> str:
    """Return a 1-2 sentence LLM summary of today's market-news headlines."""
    collected: list[str] = []
    per_topic = get_headlines_per_topic()
    for topic in get_headline_topics():
        try:
            collected.extend(_fetch_headlines(topic, per_topic))
        except Exception as exc:  # one bad topic shouldn't sink the rest
            collected.append(f"(could not load '{topic}': {exc})")

    headlines = [h for h in collected if not h.startswith("(could not load")]
    if not headlines:
        return "No market headlines available right now."

    joined = "\n".join(f"- {h}" for h in headlines)
    summary = llm(
        "local",
        f"Here are today's market-news headlines:\n{joined}\n\n"
        "Summarize the key market theme in 1-2 concise sentences. "
        "Describe what is happening only — do NOT give buy/sell advice, "
        "price predictions, or recommendations. No preamble.",
        max_tokens=160,
    )
    return summary.strip()


def headlines_node(state: StockDigestState) -> StockDigestState:
    try:
        return {"headlines": fetch_headlines()}
    except Exception as exc:
        return {"headlines": f"⚠️ Headlines unavailable ({exc})"}


if __name__ == "__main__":
    print(fetch_headlines())
