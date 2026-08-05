"""Run the interactive stock Discord bot (stays connected, listens for commands).

  uv run python scripts/run_discord_bot.py

Requires DISCORD_BOT_TOKEN in .env, the bot invited to your server with the
`applications.commands` scope, and (for @mention natural language) the
"Message Content Intent" enabled in the Discord developer portal.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shell.discord_bot import run

if __name__ == "__main__":
    run()
