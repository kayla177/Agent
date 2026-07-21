# Plan 6 — Gmail Auto-Detection (final)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A "Sync Gmail" action that scans recent emails, fuzzy-matches them to logged applications, and auto-advances their status (marking each change `auto_detected`, surfaced as the `✉` badge from Plan 3). Implemented as a dedicated `gmail_sync` agent triggered from the Applications tab and streamed via the existing SSE.

**Architecture:** A new `gmail_sync` agent (one node, `scan_gmail`) reads Gmail via the Google API Python client (the same OAuth mechanism `morning_briefing/nodes/calendar.py` uses — NOT MCP, which isn't available to the server). The node loads applications, matches emails by company, infers status from keywords, and calls `store.update_status(id, status, auto_detected=True)`. The Applications page gets a "Sync Gmail" button that POSTs `/agents/gmail_sync/run`, streams progress over SSE, and `router.refresh()`es on completion.

**Tech Stack:** Python + LangGraph + `google-api-python-client` (already a dep); Next.js EventSource (reuses Plan 5's SSE); Prisma-backed store.

## Global Constraints

- **The agent reads Gmail server-side via `google-api-python-client`**, mirroring `calendar.py`'s `_load_credentials` pattern. MCP Gmail tools are assistant-only and unavailable to the running FastAPI/LangGraph process — do not attempt to use them.
- **Graceful degradation is mandatory.** The current OAuth token is calendar-scope only; Gmail needs `gmail.readonly` re-consent, and credentials may be entirely unconfigured (no client file). Every failure path (no client file, no token, insufficient scope → 401/403, any API error) returns a friendly message and makes NO status changes — the run still completes "success". Never crash a run.
- **Auth preserves calendar.** The one-time `--auth` step requests the UNION of scopes (`calendar.readonly` + `gmail.readonly`) and overwrites the shared token, so calendar keeps working after re-consent.
- **Status changes only advance or reject** (never regress): a detected status applies only if it moves forward in the pipeline (applied→interview→offer→accepted) or is a rejection. Every auto change sets `auto_detected=1` (the `✉` badge).
- **Reuse, don't re-plumb:** `gmail_sync` is a normal `AgentSpec` (JSON trigger + SSE already exist from Plan 5); the button reuses `/agents/{key}/run` + `/runs/{id}/events` (proxied). The `send` param is accepted but unused (no Discord delivery).
- **No new deps.** `google-api-python-client`/`google-auth-oauthlib` are already in `pyproject.toml`.
- Verify the PURE matching/inference logic with offline tests (the substance) + the node's graceful no-auth path; live Gmail sync requires the user's Google OAuth setup (documented, not testable here).

---

## File structure (Plan 6)

```
agents/gmail_sync/
  __init__.py
  matching.py            # PURE: normalize_company, company_matches, infer_status, rank_status, should_apply
  gmail.py               # OAuth (gmail.readonly) + fetch_job_emails + `--auth` CLI (union scopes); graceful
  node.py                # scan_gmail_node: load apps → match emails → update_status(auto_detected=True) → summary
  state.py               # GmailSyncState
  graph.py               # build_gmail_sync_graph(send=False)
agents/registry.py       # register the gmail_sync AgentSpec
tests/test_gmail_sync.py # offline tests: pure fns + node with monkeypatched fetch + temp DB
web-next/src/
  components/applications/SyncGmail.tsx   # "use client" — button → trigger → SSE → refresh
  app/applications/page.tsx               # add <SyncGmail/>
```

---

## Task 1: Gmail agent package (matching logic + node + graph) + tests

**Files:**
- Create: `agents/gmail_sync/__init__.py` (empty), `matching.py`, `gmail.py`, `node.py`, `state.py`, `graph.py`
- Create: `tests/test_gmail_sync.py`

**Interfaces produced:** `build_gmail_sync_graph(send=False)`; pure `matching` fns; `gmail.fetch_job_emails`; `node.scan_gmail_node`.

- [ ] **Step 1: `agents/gmail_sync/__init__.py`** — empty file.

- [ ] **Step 2: `agents/gmail_sync/matching.py`** (pure, unit-tested)

```python
"""Pure heuristics for matching recruiter emails to applications.

No I/O — every function here is deterministic and unit-tested. The Gmail node
uses these to decide which application (if any) an email refers to and what
status it implies. Kept intentionally conservative: only advance the pipeline
or mark a rejection, and always flag the change as auto-detected.
"""

from __future__ import annotations

import re

# Detection keywords, most-significant first. A rejection signal outranks an
# offer, which outranks an interview (a later rejection overrides earlier news).
_STATUS_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("rejected", (
        "unfortunately", "not moving forward", "not be moving forward",
        "decided not to", "other candidates", "will not be proceeding",
        "regret to inform", "unable to offer", "not to move forward",
        "won't be moving", "no longer under consideration",
    )),
    ("offer", (
        "pleased to offer", "offer of employment", "excited to offer",
        "job offer", "extend an offer", "offer letter",
    )),
    ("interview", (
        "interview", "schedule a call", "schedule a time", "next steps",
        "meet the team", "phone screen", "technical screen", "set up a call",
        "availability", "hiring manager", "coding challenge", "assessment",
    )),
]

_DETECT_RANK = {"interview": 1, "offer": 2, "rejected": 3}
# Pipeline progression (rejected handled specially in should_apply).
_PIPELINE_RANK = {"applied": 0, "interview": 1, "offer": 2, "accepted": 3}


def normalize_company(s: str) -> str:
    """Lowercase, drop common suffixes/noise, keep only [a-z0-9]."""
    n = (s or "").lower()
    n = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|co|gmbh|technologies|the)\b", " ", n)
    return re.sub(r"[^a-z0-9]", "", n)


def company_matches(app_company: str, haystack: str) -> bool:
    """True if the (normalized) application company appears in the haystack
    (sender name + subject + snippet, normalized). Guards against 1-2 char
    company keys that would match noise."""
    key = normalize_company(app_company)
    if len(key) < 3:
        return False
    return key in normalize_company(haystack)


def infer_status(text: str) -> str | None:
    """Most-significant status implied by the text, or None."""
    t = (text or "").lower()
    for status, kws in _STATUS_KEYWORDS:
        if any(k in t for k in kws):
            return status
    return None


def rank_status(status: str) -> int:
    """Detection significance (higher = more significant signal)."""
    return _DETECT_RANK.get(status, 0)


def should_apply(current: str, detected: str) -> bool:
    """Apply a detected status only if it ADVANCES the pipeline or is a fresh
    rejection — never regress (e.g. offer -> interview is rejected)."""
    if detected == "rejected":
        return current != "rejected"
    if detected in _PIPELINE_RANK and current in _PIPELINE_RANK:
        return _PIPELINE_RANK[detected] > _PIPELINE_RANK[current]
    return detected != current
```

- [ ] **Step 3: `agents/gmail_sync/state.py`**

```python
"""Shared state for the gmail-sync graph."""

from __future__ import annotations

from typing import TypedDict


class GmailSyncState(TypedDict, total=False):
    message: str  # summary of what the scan changed (the registry output_key)
```

- [ ] **Step 4: `agents/gmail_sync/gmail.py`** (OAuth + fetch + `--auth`; graceful)

```python
"""Gmail read access for the sync agent — mirrors calendar.py's OAuth flow.

Reads the SHARED Google token (config.GOOGLE_TOKEN_FILE). The node calls
fetch_job_emails(allow_interactive=False), which returns None whenever Gmail
isn't usable (no token, no scope, no client file, or a 401/403) so the node can
degrade gracefully. One-time consent:

    uv run python -m agents.gmail_sync.gmail --auth

which mints a token with BOTH calendar + gmail scopes (so calendar keeps
working) and caches it to config.GOOGLE_TOKEN_FILE.
"""

from __future__ import annotations

import re

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

# The node only needs gmail read; the interactive auth requests the union so a
# re-consent doesn't strip the calendar scope the briefing agent relies on.
_LOAD_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_AUTH_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Recruiter-ish signal in the last 30 days. Broad on purpose; matching.py
# decides relevance per application.
_QUERY = (
    'newer_than:30d (interview OR "next steps" OR offer OR unfortunately OR '
    '"moving forward" OR application OR "phone screen" OR schedule OR '
    '"thank you for applying" OR "your application")'
)


def _load_credentials(*, allow_interactive: bool, scopes: list[str]) -> Credentials | None:
    creds: Credentials | None = None
    if config.GOOGLE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN_FILE), scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            config.GOOGLE_TOKEN_FILE.write_text(creds.to_json())
            return creds
        except Exception:
            return None
    if not allow_interactive:
        return None
    if not config.GOOGLE_OAUTH_CLIENT_FILE.exists():
        raise FileNotFoundError(
            f"OAuth client file not found: {config.GOOGLE_OAUTH_CLIENT_FILE}. "
            "Download it from Google Cloud Console (OAuth client, Desktop app)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_OAUTH_CLIENT_FILE), scopes)
    creds = flow.run_local_server(port=0)
    config.GOOGLE_TOKEN_FILE.write_text(creds.to_json())
    return creds


def _parse_from(value: str) -> tuple[str, str]:
    """Split a 'Name <email>' From header into (name, email)."""
    m = re.match(r"\s*(.*?)\s*<([^>]+)>\s*$", value or "")
    if m:
        return m.group(1).strip().strip('"'), m.group(2).strip()
    return "", (value or "").strip()


def fetch_job_emails(*, allow_interactive: bool = False, max_results: int = 40) -> list[dict] | None:
    """Return recent job-related emails as {from_name, from_email, subject,
    snippet}, or None if Gmail isn't authorized/usable (caller degrades)."""
    creds = _load_credentials(allow_interactive=allow_interactive, scopes=_LOAD_SCOPES)
    if creds is None:
        return None
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        listing = (
            service.users().messages()
            .list(userId="me", q=_QUERY, maxResults=max_results)
            .execute()
        )
        out: list[dict] = []
        for ref in listing.get("messages", []):
            msg = (
                service.users().messages()
                .get(userId="me", id=ref["id"], format="metadata",
                     metadataHeaders=["From", "Subject"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            name, email = _parse_from(headers.get("From", ""))
            out.append({
                "from_name": name, "from_email": email,
                "subject": headers.get("Subject", ""), "snippet": msg.get("snippet", ""),
            })
        return out
    except HttpError as exc:
        if exc.resp.status in (401, 403):
            return None  # not authorized / insufficient scope → degrade
        raise


if __name__ == "__main__":
    # One-time consent with the union scopes (preserves calendar access).
    creds = _load_credentials(allow_interactive=True, scopes=_AUTH_SCOPES)
    print("Authorized." if creds else "Authorization failed.")
```

- [ ] **Step 5: `agents/gmail_sync/node.py`**

```python
"""scan_gmail node — match recent emails to applications, advance statuses."""

from __future__ import annotations

from agents.application_tracker.store import load_all, update_status
from agents.gmail_sync.gmail import fetch_job_emails
from agents.gmail_sync.matching import company_matches, infer_status, rank_status, should_apply
from agents.gmail_sync.state import GmailSyncState

_AUTH_HINT = (
    "⚠️ Gmail not authorized. Run `uv run python -m agents.gmail_sync.gmail --auth` "
    "once to grant read access (re-consents calendar too)."
)


def scan_gmail_node(state: GmailSyncState) -> GmailSyncState:
    apps = load_all()
    if not apps:
        return {"message": "✉️ Gmail sync: no applications logged to match against."}

    try:
        emails = fetch_job_emails(allow_interactive=False)
    except Exception as exc:  # never crash a run
        return {"message": f"⚠️ Gmail scan failed ({exc})."}
    if emails is None:
        return {"message": _AUTH_HINT}

    updates: list[str] = []
    for app in apps:
        detected: str | None = None
        for em in emails:
            haystack = f"{em['from_name']} {em['subject']} {em['snippet']}"
            if company_matches(app.get("company", ""), haystack):
                st = infer_status(f"{em['subject']} {em['snippet']}")
                if st and (detected is None or rank_status(st) > rank_status(detected)):
                    detected = st
        if detected and should_apply(app.get("status", ""), detected):
            update_status(int(app["id"]), detected, auto_detected=True)
            updates.append(f"{app['company']}: {app.get('status')} → {detected}")

    n = len(emails)
    if updates:
        body = "\n".join(f"• {u}" for u in updates)
        return {"message": f"✉️ Gmail sync scanned {n} emails and updated {len(updates)} application(s):\n\n{body}"}
    return {"message": f"✉️ Gmail sync scanned {n} emails — no status changes detected."}
```

- [ ] **Step 6: `agents/gmail_sync/graph.py`**

```python
"""LangGraph definition for the gmail-sync agent (single node)."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.gmail_sync.node import scan_gmail_node
from agents.gmail_sync.state import GmailSyncState


def build_gmail_sync_graph(*, send: bool = False):
    """Compile the one-node gmail-sync graph. `send` is accepted for the
    registry contract but unused (no Discord delivery)."""
    g = StateGraph(GmailSyncState)
    g.add_node("scan_gmail", scan_gmail_node)
    g.add_edge(START, "scan_gmail")
    g.add_edge("scan_gmail", END)
    return g.compile()
```

- [ ] **Step 7: `tests/test_gmail_sync.py`** (offline — pure fns + node via monkeypatched fetch)

```python
"""Offline tests for the gmail-sync agent (no network, no pytest).

Run:  uv run python tests/test_gmail_sync.py

Covers the pure matching/inference heuristics and the node's behavior against a
canned email list + a temp SQLite store (fetch_job_emails is monkeypatched).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store_db
from agents.application_tracker import store as appstore
from agents.gmail_sync import matching
from agents.gmail_sync import node as gnode

_failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _failures.append(name)


def test_matching() -> None:
    print("matching heuristics")
    check("normalize strips suffix+punct", matching.normalize_company("Stripe, Inc.") == "stripe")
    check("company match in subject", matching.company_matches("Stripe", "Re: your application to Stripe"))
    check("company match via sender name", matching.company_matches("Notion", "Notion Recruiting <x@greenhouse.io>"))
    check("no match unrelated", matching.company_matches("Figma", "Weekly newsletter from Medium") is False)
    check("short key guarded", matching.company_matches("AB", "AB Corp interview") is False)
    check("infer rejected", matching.infer_status("Unfortunately we decided not to move forward") == "rejected")
    check("infer offer", matching.infer_status("We are pleased to offer you the role") == "offer")
    check("infer interview", matching.infer_status("Let's schedule a call for next steps") == "interview")
    check("infer none", matching.infer_status("Your weekly digest") is None)
    check("rank rejected>offer>interview", matching.rank_status("rejected") > matching.rank_status("offer") > matching.rank_status("interview"))
    check("apply advances", matching.should_apply("applied", "interview") is True)
    check("apply no regress", matching.should_apply("offer", "interview") is False)
    check("apply rejection always", matching.should_apply("offer", "rejected") is True)
    check("apply rejection idempotent", matching.should_apply("rejected", "rejected") is False)


def test_node_with_fake_gmail() -> None:
    print("scan_gmail node (monkeypatched fetch + temp DB)")
    store_db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    store_db.init_db()
    appstore.add_application("Stripe", "SWE Intern", status="applied")   # id 1
    appstore.add_application("Figma", "FE Intern", status="interview")   # id 2
    appstore.add_application("Notion", "PM Intern", status="applied")    # id 3

    fake_emails = [
        {"from_name": "Stripe Recruiting", "from_email": "jobs@greenhouse.io",
         "subject": "Next steps for your Stripe application", "snippet": "Let's schedule a call."},
        {"from_name": "Figma Talent", "from_email": "no-reply@figma.com",
         "subject": "Figma — update", "snippet": "Unfortunately we won't be moving forward."},
        # Notion: no email → unchanged
    ]
    gnode.fetch_job_emails = lambda **_: fake_emails  # monkeypatch the name in node's namespace

    result = gnode.scan_gmail_node({})
    apps = {a["company"]: a for a in appstore.load_all()}
    check("Stripe advanced applied→interview", apps["Stripe"]["status"] == "interview")
    check("Stripe flagged auto_detected", apps["Stripe"]["auto_detected"] is True)
    check("Figma interview→rejected", apps["Figma"]["status"] == "rejected")
    check("Notion unchanged (no email)", apps["Notion"]["status"] == "applied")
    check("Notion not auto_detected", apps["Notion"]["auto_detected"] is False)
    check("message mentions 2 updates", "updated 2" in result["message"])


def test_node_not_authorized() -> None:
    print("scan_gmail node (not authorized → graceful)")
    store_db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    store_db.init_db()
    appstore.add_application("Stripe", "SWE Intern", status="applied")
    gnode.fetch_job_emails = lambda **_: None  # simulate no-auth
    result = gnode.scan_gmail_node({})
    check("returns auth hint", "not authorized" in result["message"].lower())
    check("no status change", appstore.load_all()[0]["status"] == "applied")


def main() -> int:
    for fn in (test_matching, test_node_with_fake_gmail, test_node_not_authorized):
        fn()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("all gmail-sync tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Run tests + import checks**

Run:
```bash
cd /Users/kayla.li/.superset/Agent
uv run python tests/test_gmail_sync.py
uv run python -c "from agents.gmail_sync.graph import build_gmail_sync_graph; build_gmail_sync_graph(send=False); print('graph builds')"
```
Expected: all checks pass (`all gmail-sync tests passed`); `graph builds`.

- [ ] **Step 9: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add agents/gmail_sync tests/test_gmail_sync.py
git commit -m "feat(gmail): gmail-sync agent (email→application matching + status inference) with offline tests"
```

---

## Task 2: Register the agent + verify triggerable

**Files:**
- Modify: `agents/registry.py`

- [ ] **Step 1: Add the builder + spec to `agents/registry.py`**

Add a builder function alongside the others:
```python
def _gmail_sync_builder():
    from agents.gmail_sync.graph import build_gmail_sync_graph

    return build_gmail_sync_graph
```
And add to the `REGISTRY` dict:
```python
    "gmail_sync": AgentSpec(
        key="gmail_sync",
        display_name="Gmail Sync",
        description="Scan recent emails and auto-update application statuses.",
        emoji="✉️",
        _builder=_gmail_sync_builder,
        node_order=("scan_gmail",),
    ),
```

- [ ] **Step 2: Verify registration + a live trigger through FastAPI**

```bash
cd /Users/kayla.li/.superset/Agent
uv run python -c "from agents.registry import get_spec; print(get_spec('gmail_sync').display_name)"
uv run python -m web > /tmp/fastapi_gmail.log 2>&1 &
sleep 4
RID=$(curl -s -X POST "localhost:8001/agents/gmail_sync/run?send=0" | node -e "process.stdin.on('data',d=>{try{console.log(JSON.parse(d).run_id)}catch(e){console.log('ERR '+d)}})")
echo "run_id=$RID"
echo "SSE (should stream scan_gmail node + a done frame with the summary/auth message):"
curl -s --max-time 4 "localhost:8001/runs/$RID/events"; echo
echo "run row:"; sqlite3 data/control_center.db "SELECT id, agent_key, status FROM runs WHERE id=$RID;"
lsof -ti:8001 | xargs kill 2>/dev/null
```
Expected: `Gmail Sync`; a numeric run_id; the SSE emits an `event: node` for `scan_gmail` then an `event: done` whose HTML contains either the "no status changes"/"updated N" summary or the "not authorized" hint (whichever applies given the machine's Gmail creds — both are success outcomes); the run row is `success`. No crash regardless of Gmail auth state.

- [ ] **Step 3: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add agents/registry.py
git commit -m "feat(gmail): register gmail_sync agent"
```

---

## Task 3: "Sync Gmail" button on the Applications tab

**Files:**
- Create: `web-next/src/components/applications/SyncGmail.tsx`
- Modify: `web-next/src/app/applications/page.tsx`

- [ ] **Step 1: `web-next/src/components/applications/SyncGmail.tsx`**

```tsx
"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

export default function SyncGmail() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [resultHtml, setResultHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function sync() {
    setBusy(true);
    setResultHtml(null);
    setError(null);
    const res = await fetch("/agents/gmail_sync/run?send=0", { method: "POST" });
    if (!res.ok) {
      setBusy(false);
      setError("Could not start Gmail sync.");
      return;
    }
    const { run_id } = await res.json();
    const es = new EventSource(`/runs/${run_id}/events`);
    es.addEventListener("done", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setResultHtml(d.html || "");
      es.close();
      setBusy(false);
      router.refresh(); // reload the table so updated statuses + ✉ badges show
    });
    es.addEventListener("failed", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setError(d.error || "Gmail sync failed.");
      es.close();
      setBusy(false);
    });
  }

  return (
    <div className="sync-gmail">
      <button className="primary" onClick={sync} disabled={busy}>
        {busy ? "Syncing Gmail…" : "✉️ Sync Gmail"}
      </button>
      {resultHtml !== null ? <div className="output" dangerouslySetInnerHTML={{ __html: resultHtml }} /> : null}
      {error ? <p className="banner err">{error}</p> : null}
    </div>
  );
}
```

Note: `d.html` is server-rendered markdown from the trusted local agent (renderer hardened to `html:False` in Plan 5), so `dangerouslySetInnerHTML` is safe.

- [ ] **Step 2: Add `<SyncGmail/>` to `web-next/src/app/applications/page.tsx`**

Add the import at the top:
```tsx
import SyncGmail from "@/components/applications/SyncGmail";
```
Then render it right after the `<StatsRow stats={stats} />` line:
```tsx
      <StatsRow stats={stats} />
      <SyncGmail />
```

- [ ] **Step 3: (optional CSS) small spacing for the sync block**

Append to `web-next/src/app/globals.css`:
```css
.sync-gmail { margin: .5rem 0 1rem; }
.sync-gmail .output { margin-top: .6rem; }
```

- [ ] **Step 4: Type-check + build**

Run: `cd web-next && npx tsc --noEmit && npm run build`
Expected: exit 0.

- [ ] **Step 5: Verify the button end-to-end (both servers) + Playwright (controller)**

```bash
cd /Users/kayla.li/.superset/Agent
uv run python -m web > /tmp/fastapi_gmail.log 2>&1 &
(cd web-next && npm run dev -- -p 3007 > /tmp/next_gmail.log 2>&1 &)
sleep 9
echo "applications page has the Sync button:"; curl -s localhost:3007/applications | grep -c "Sync Gmail"   # >= 1
echo "trigger through the proxy:"; curl -s -X POST "localhost:3007/agents/gmail_sync/run?send=0" -w "\n%{http_code}\n"
lsof -ti:8001 | xargs kill 2>/dev/null; lsof -ti:3007 | xargs kill 2>/dev/null
```
Expected: "Sync Gmail" present; the proxied trigger returns `{"run_id":N}` + 200. Controller then does a Playwright pass: open `/applications`, click "✉️ Sync Gmail", confirm the result line appears (either "no status changes" / "updated N" or the not-authorized hint) and the table refreshes — screenshot it. Kill servers.

- [ ] **Step 6: Commit**

```bash
cd /Users/kayla.li/.superset/Agent
git add web-next/src/components/applications/SyncGmail.tsx web-next/src/app/applications/page.tsx web-next/src/app/globals.css
git commit -m "feat(web-next): Sync Gmail button on the applications tab (triggers gmail_sync, streams, refreshes)"
```

---

## Plan 6 verification summary

- Offline tests: matching heuristics (normalize/company-match/infer/rank/should_apply) + the node against canned emails (Stripe applied→interview, Figma interview→rejected, Notion unchanged, all `auto_detected` correct) + the not-authorized graceful path — all pass.
- `gmail_sync` registered; a live trigger returns a run_id and streams a node + done frame; the run succeeds whether or not Gmail is authorized (graceful message either way).
- Applications tab shows "✉️ Sync Gmail"; clicking it triggers the agent, streams the result, and refreshes the table (updated statuses show the `✉` badge from Plan 3).
- `npx tsc --noEmit` + `npm run build` clean; Python suites green.

## One-time setup for real Gmail (user action, outside this plan)
Real syncing requires a Google OAuth client + Gmail consent:
1. Put the OAuth Desktop client JSON at `config.GOOGLE_OAUTH_CLIENT_FILE`.
2. `uv run python -m agents.gmail_sync.gmail --auth` (opens a browser; grants calendar+gmail read).
Until then, "Sync Gmail" reports "not authorized" and changes nothing — by design.

## Self-review notes
- **MCP vs API:** correctly uses `google-api-python-client` (server-side), not the assistant-only MCP.
- **Graceful everywhere:** no token / no client file / insufficient scope (401/403) / API error all return friendly messages, zero status changes, run still succeeds.
- **Calendar preserved:** `--auth` requests the union scope set so re-consent doesn't strip calendar.
- **No regressions:** `should_apply` only advances or marks rejected; every change sets `auto_detected` (the `✉` badge already rendered by Plan 3's StatusPill).
- **Reuses Plan 5 machinery:** normal AgentSpec + existing JSON trigger + SSE + proxy; the button mirrors the dashboard's trigger→EventSource pattern and adds `router.refresh()`.
- **Testable core:** the heuristics + node logic are pure/monkeypatched and fully unit-tested offline; only live Gmail I/O is out of test scope (documented).
