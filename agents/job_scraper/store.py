"""JSON seen-store for posting ids (the dedupe memory).

Stored at agents/job_scraper/data/seen.json as a JSON object:
    {"ids": ["<global id>", ...]}

The dir/file are created lazily. Reads degrade gracefully (a missing or corrupt
file is treated as "nothing seen yet") so a bad file never crashes a run.
"""

from __future__ import annotations

import json
from pathlib import Path

# .../agents/job_scraper/data/seen.json  (relative to this file, not cwd)
_DATA_DIR = Path(__file__).resolve().parent / "data"
_STORE = _DATA_DIR / "seen.json"


def load_seen() -> set[str]:
    """Return the set of posting ids seen on previous runs (empty if none)."""
    try:
        with _STORE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return set(data.get("ids", []))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()


def add_seen(ids: list[str]) -> None:
    """Merge `ids` into the seen-store, creating the dir/file if missing."""
    if not ids:
        return
    seen = load_seen()
    seen.update(ids)
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump({"ids": sorted(seen)}, fh, indent=2)
    tmp.replace(_STORE)  # atomic-ish swap so an interrupted write can't corrupt
