"""Interactive Discord bot for the stock agent.

Two ways to talk to it in your channel:
  • Slash commands: /portfolio /signals /quote /digest /trade /help
  • @mention in plain English → the local model classifies intent and routes to
    the same actions.

All heavy work runs in a threadpool (asyncio.to_thread) so the gateway loop
never blocks. Long replies are chunked to Discord's 2000-char limit. Run via
scripts/run_discord_bot.py. Needs the "Message Content Intent" enabled for the
natural-language path.
"""

from __future__ import annotations

import asyncio
import json

import discord
from discord import app_commands
from discord.ext import commands

import config
from agents.stock_bot import actions
from shell.model_router import llm

_MAX = 1900  # under Discord's 2000-char hard limit

_INTENTS = discord.Intents.default()
_INTENTS.message_content = True  # required to read @mention text

bot = commands.Bot(command_prefix="!", intents=_INTENTS)


def _chunks(text: str) -> list[str]:
    out, buf = [], ""
    for line in (text or "…").split("\n"):
        if len(buf) + len(line) + 1 > _MAX:
            if buf:
                out.append(buf)
            buf = line[:_MAX]
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        out.append(buf)
    return out or ["…"]


async def _reply(send, fn, *args):
    """Run a (blocking) action off-thread and send the (chunked) reply."""
    try:
        text = await asyncio.to_thread(fn, *args)
    except Exception as exc:
        text = f"⚠️ Something went wrong: {exc}"
    for chunk in _chunks(text):
        await send(chunk)


# ---------------- Slash commands ----------------
@bot.tree.command(name="help", description="What the stock agent can do")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(actions.help_text())


@bot.tree.command(name="portfolio", description="Paper account value, cash, positions & P&L")
async def portfolio_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await _reply(interaction.followup.send, actions.portfolio_text)


@bot.tree.command(name="signals", description="Live BUY/SELL/HOLD per watchlist ticker")
async def signals_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await _reply(interaction.followup.send, actions.signals_text)


@bot.tree.command(name="quote", description="A single quote")
@app_commands.describe(symbol="Ticker, e.g. AAPL")
async def quote_cmd(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer(thinking=True)
    await _reply(interaction.followup.send, actions.quote_text, symbol)


@bot.tree.command(name="digest", description="Run the full Stock Digest")
async def digest_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await _reply(interaction.followup.send, actions.digest_text)


@bot.tree.command(name="trade", description="Run the paper trader now (places PAPER orders, guarded)")
async def trade_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await _reply(interaction.followup.send, actions.trade_text)


# ---------------- Natural language (@mention) ----------------
_ACTIONS = {
    "portfolio": actions.portfolio_text,
    "signals": actions.signals_text,
    "digest": actions.digest_text,
    "trade": actions.trade_text,
    "help": actions.help_text,
}


def _classify(text: str) -> dict:
    """Local-LLM intent classifier -> {"action":..., "symbol":...}."""
    raw = llm(
        "local",
        "Classify the user's request into exactly one action: portfolio, signals, "
        "quote, digest, trade, or help. If it's quote, extract the ticker SYMBOL. "
        'Respond ONLY as compact JSON like {"action":"quote","symbol":"AAPL"} '
        "(symbol null if not a quote).\nRequest: " + text,
        max_tokens=60,
    )
    try:
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
        action = data.get("action", "help")
        return {"action": action if action in ({"quote"} | set(_ACTIONS)) else "help",
                "symbol": (data.get("symbol") or "")}
    except Exception:
        return {"action": "help", "symbol": ""}


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or bot.user not in message.mentions:
        return
    if config.DISCORD_STOCK_CHANNEL_ID and str(message.channel.id) != str(config.DISCORD_STOCK_CHANNEL_ID):
        return
    text = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
    async with message.channel.typing():
        intent = await asyncio.to_thread(_classify, text)
        if intent["action"] == "quote":
            await _reply(message.channel.send, actions.quote_text, intent["symbol"])
        else:
            await _reply(message.channel.send, _ACTIONS.get(intent["action"], actions.help_text))


@bot.event
async def on_ready():
    try:
        if config.DISCORD_GUILD_ID:
            guild = discord.Object(id=int(config.DISCORD_GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
        else:
            synced = await bot.tree.sync()
        print(f"✅ logged in as {bot.user} — {len(synced)} slash command(s) synced")
    except Exception as exc:
        print(f"⚠️ command sync failed: {exc}")


def run() -> None:
    token = config.DISCORD_BOT_TOKEN
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN not set in .env")
    bot.run(token)
