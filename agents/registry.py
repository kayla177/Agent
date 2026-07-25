"""Uniform registry over the platform's agents.

Every agent exposes a ``build_*_graph(*, send: bool)`` that returns a compiled
LangGraph whose final output lives in ``state["message"]`` (Discord-flavored
markdown). The web layer drives any agent generically through this registry —
it never imports individual node modules.

Builders are imported lazily inside each spec's factory so that importing this
module does not pull in litellm / httpx / google libs until a run actually
starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class AgentSpec:
    key: str  # stable id used in URLs + DB rows
    display_name: str
    description: str
    emoji: str
    _builder: Callable[[], Callable[..., Any]]  # returns the build_*_graph fn
    output_key: str = "message"
    node_order: tuple[str, ...] = field(default_factory=tuple)  # for UI display
    planet: str = "earth"  # UI theme (earth | jupiter | mars | saturn)
    label: str = ""        # short UI label; falls back to key

    def build_graph(self, *, send: bool = False):
        """Compile and return this agent's graph. ``send`` toggles delivery."""
        return self._builder()(send=send)

    def to_meta(self) -> dict:
        """UI-facing metadata (the single source consumed via GET /agents)."""
        return {
            "key": self.key,
            "name": self.display_name,
            "description": self.description,
            "emoji": self.emoji,
            "planet": self.planet,
            "label": self.label or self.key,
            "node_order": list(self.node_order),
        }


def _briefing_builder():
    from agents.morning_briefing.graph import build_briefing_graph

    return build_briefing_graph


def _stock_builder():
    from agents.stock_digest.graph import build_stock_digest_graph

    return build_stock_digest_graph


def _job_builder():
    from agents.job_scraper.graph import build_job_scraper_graph

    return build_job_scraper_graph


def _tracker_builder():
    from agents.application_tracker.graph import build_tracker_graph

    return build_tracker_graph


def _resume_builder():
    from agents.resume_generator.graph import build_resume_generator_graph

    return build_resume_generator_graph


def _gmail_sync_builder():
    from agents.gmail_sync.graph import build_gmail_sync_graph

    return build_gmail_sync_graph


REGISTRY: dict[str, AgentSpec] = {
    "morning_briefing": AgentSpec(
        key="morning_briefing",
        display_name="Morning Briefing",
        description="Weather, commute & traffic, calendar, and news catch-up.",
        emoji="🌅",
        _builder=_briefing_builder,
        node_order=("weather", "commute", "calendar", "news", "synthesize", "deliver"),
        planet="earth", label="briefing",
    ),
    "stock_digest": AgentSpec(
        key="stock_digest",
        display_name="Stock Digest",
        description="Plain-English verdicts, explained signals, news & market overview (info only, not advice).",
        emoji="📈",
        _builder=_stock_builder,
        node_order=("market_data", "technical", "market_overview", "news_sentiment",
                    "analyst", "synthesize", "persist", "deliver"),
        planet="jupiter", label="stocks",
    ),
    "job_scraper": AgentSpec(
        key="job_scraper",
        display_name="Job Scraper",
        description="New co-op / intern / new-grad roles from official ATS boards.",
        emoji="🧑‍💻",
        _builder=_job_builder,
        node_order=("fetch", "filter", "dedupe", "freshness", "rank", "notify"),
        planet="mars", label="jobs",
    ),
    "application_tracker": AgentSpec(
        key="application_tracker",
        display_name="Application Tracker",
        description="Your application pipeline, follow-up reminders, and interviews.",
        emoji="📋",
        _builder=_tracker_builder,
        node_order=("summary", "followups", "synthesize", "deliver"),
        planet="saturn", label="tracker",
    ),
    "resume_generator": AgentSpec(
        key="resume_generator",
        display_name="Resume Generator",
        description="Draft an ATS-tailored resume for a scraped job (per-job, not scheduled).",
        emoji="📝",
        _builder=_resume_builder,
        node_order=("gather", "research", "keywords", "draft", "save"),
        planet="jupiter", label="resume",
    ),
    "gmail_sync": AgentSpec(
        key="gmail_sync",
        display_name="Gmail Sync",
        description="Scan recent emails and auto-update application statuses.",
        emoji="✉️",
        _builder=_gmail_sync_builder,
        node_order=("scan_gmail",),
        planet="earth", label="gmail",
    ),
}


def list_specs() -> list[AgentSpec]:
    """All agent specs, in display order."""
    return list(REGISTRY.values())


def get_spec(key: str) -> AgentSpec:
    """Look up a spec by key; raise KeyError with a clear message if unknown."""
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"unknown agent '{key}' (expected one of {sorted(REGISTRY)})"
        ) from None
