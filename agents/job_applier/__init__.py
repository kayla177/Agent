"""job_applier — visible-browser assisted job application filler.

Phase B: opens a Greenhouse/Lever/Ashby application in a visible Chromium
window, fills what it can from the user's profile, drafts free-text answers
with the local model (clearly marked as AI-drafted), then stops for human
review. No code path in this package may ever click a submit button.
"""

from __future__ import annotations
