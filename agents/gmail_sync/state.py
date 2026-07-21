"""Shared state for the gmail-sync graph."""

from __future__ import annotations

from typing import TypedDict


class GmailSyncState(TypedDict, total=False):
    message: str  # summary of what the scan changed (the registry output_key)
