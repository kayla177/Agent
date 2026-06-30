"""News node — Google News RSS per topic, summarized by the local model.

RSS is free and needs no key. Headlines are passed to the local LLM (cheap,
private) to produce a tight one-line summary per topic.
"""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET

import httpx

import config
from agents.morning_briefing.state import BriefingState
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


def fetch_news() -> str:
    """Return a compact, LLM-summarized news catch-up across configured topics."""
    blocks: list[str] = []
    for topic in config.NEWS_TOPICS:
        headlines = _fetch_headlines(topic, config.NEWS_MAX_ITEMS_PER_TOPIC)
        if not headlines:
            blocks.append(f"**{topic}**: (no headlines)")
            continue
        joined = "\n".join(f"- {h}" for h in headlines)
        summary = llm(
            "local",
            f"Here are today's news headlines about '{topic}':\n{joined}\n\n"
            "Write ONE concise sentence summarizing the key theme. No preamble.",
            max_tokens=120,
        )
        blocks.append(f"**{topic}**: {summary}")
    return "\n".join(blocks)


def news_node(state: BriefingState) -> BriefingState:
    try:
        return {"news": fetch_news()}
    except Exception as exc:
        return {"news": f"⚠️ News unavailable ({exc})"}


if __name__ == "__main__":
    print(fetch_news())
