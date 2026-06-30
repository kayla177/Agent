"""Discord delivery via the REST API.

For one-shot notifications we POST straight to the channel-messages endpoint
with the bot token — no gateway/websocket needed. The bot must be a member of
the target server with permission to post in the channel.

Requires DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID in .env.
"""

from __future__ import annotations

import httpx

import config

_API = "https://discord.com/api/v10"
_MAX_LEN = 2000  # Discord per-message character limit


def _chunks(text: str, size: int = _MAX_LEN) -> list[str]:
    """Split on line boundaries where possible, hard-split only if a line is huge."""
    out: list[str] = []
    buf = ""
    for line in text.split("\n"):
        candidate = f"{buf}\n{line}" if buf else line
        if len(candidate) <= size:
            buf = candidate
            continue
        if buf:
            out.append(buf)
        while len(line) > size:
            out.append(line[:size])
            line = line[size:]
        buf = line
    if buf:
        out.append(buf)
    return out or [""]


def send_message(text: str, channel_id: str | None = None) -> None:
    """Send `text` to the configured (or given) Discord channel, chunked if long."""
    token = config.DISCORD_BOT_TOKEN
    channel = channel_id or config.DISCORD_CHANNEL_ID
    if not token or not channel:
        raise RuntimeError("DISCORD_BOT_TOKEN / DISCORD_CHANNEL_ID not set in .env")

    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    url = f"{_API}/channels/{channel}/messages"
    with httpx.Client(timeout=20) as client:
        for chunk in _chunks(text):
            resp = client.post(url, headers=headers, json={"content": chunk})
            resp.raise_for_status()


if __name__ == "__main__":
    send_message("✅ daily-agents: Discord delivery test — platform online.")
    print("sent (check your Discord channel)")
