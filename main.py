import os
import re
import json
import time
import asyncio
import threading
from typing import Optional
from urllib.parse import quote

import requests
import urllib3
from flask import Flask

import discord
from discord import app_commands

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ----------------------------------------------------------------------
# CONFIG  (all from Render env vars)
# ----------------------------------------------------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID      = os.environ.get("GUILD_ID")
PORT          = int(os.environ.get("PORT", 10000))          # Render injects this
RENDER_URL    = os.environ.get("RENDER_EXTERNAL_URL")       # auto-set by Render

API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"

BASE_HEADERS = {
    "User-Agent": "Netflix/15.48 (iPhone; iOS 17.4; Scale/3.00)",
    "Accept": "application/json",
    "Accept-Language": "en-US;q=1.0",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}

COOLDOWN_SECONDS = 5
_last_use: dict[int, float] = {}


# ----------------------------------------------------------------------
# Netflix logic
# ----------------------------------------------------------------------
def build_cookie(raw: str) -> str:
    """Accept raw cookie string, 'Cookie:' line, or browser JSON export."""
    raw = raw.strip()

    if raw.startswith("[") or raw.startswith("{"):
        try:
            data = json.loads(raw)
            items = data if isinstance(data, list) else data.get("cookies", [])
            parts = [
                f"{c['name']}={c['value']}"
                for c in items
                if isinstance(c, dict) and "name" in c and "value" in c
            ]
            if parts:
                return "; ".join(parts)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    raw = re.sub(r"^\s*cookie\s*:\s*", "", raw, flags=re.I)
    raw = " ".join(raw.split())
    if not raw.endswith(";"):
        raw += ";"
    return raw


def generate_nftoken(cookie: str) -> str:
    resp = requests.get(
        API_URL,
        headers={**BASE_HEADERS, "Cookie": cookie},
        verify=False,
        timeout=25,
    )
    resp.raise_for_status()
    token = resp.json()["value"]["account"]["token"]["default"]["token"]
    return f"https://netflix.com/?nftoken={quote(token, safe='')}"


# ----------------------------------------------------------------------
# Web server  (Render needs something listening on PORT)
# ----------------------------------------------------------------------
web = Flask(__name__)


@web.get("/")
@web.get("/health")
def health():
    return "ok", 200


def run_web():
    web.run(host="0.0.0.0", port=PORT, use_reloader=False)


def self_ping():
    """Best-effort keep-alive so Render free tier doesn't sleep."""
    if not RENDER_URL:
        return
    while True:
        time.sleep(600)  # 10 min
        try:
            requests.get(RENDER_URL, timeout=10)
        except Exception:
            pass


# ----------------------------------------------------------------------
# Discord bot
# ----------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True   # enables "just paste the cookie" mode
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def on_cooldown(user_id: int) -> int:
    now = time.time()
    last = _last_use.get(user_id, 0)
    if now - last < COOLDOWN_SECONDS:
        return int(COOLDOWN_SECONDS - (now - last)) + 1
    _last_use[user_id] = now
    return 0


async def handle_cookie(interaction: discord.Interaction, raw: str):
    full = build_cookie(raw)
    if "NetflixId=" not in full:
        await interaction.followup.send(
            "No `NetflixId` found in that. Paste the full cookie or attach input.txt.",
            ephemeral=True,
        )
        return

    try:
        link = await asyncio.to_thread(generate_nftoken, full)
    except Exception as exc:
        await interaction.followup.send(
            f"Failed: `{type(exc).__name__}: {exc}`", ephemeral=True
        )
        return

    embed = discord.Embed(title="Netflix login link", color=0xE50914)
    embed.add_field(name="Open this", value=link, inline=False)
    embed.set_footer(text="Live session — keep the link private.")
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="nftoken", description="Turn a Netflix cookie into a login link")
@app_commands.describe(
    cookie="Paste your cookie (NetflixId=...)",
    file="...or attach input.txt",
)
async def nftoken(
    interaction: discord.Interaction,
    cookie: Optional[str] = None,
    file: Optional[discord.Attachment] = None,
):
    await interaction.response.defer(ephemeral=True)

    wait = on_cooldown(interaction.user.id)
    if wait:
        await interaction.followup.send(f"Slow down — {wait}s.", ephemeral=True)
        return

    raw = cookie
    if file is not None:
        raw = (await file.read()).decode("utf-8", "ignore")

    if not raw:
        await interaction.followup.send("Give me a cookie or attach input.txt.", ephemeral=True)
        return

    await handle_cookie(interaction, raw)


@client.event
async def on_message(message: discord.Message):
    """Optional: just paste a cookie in chat and get a link."""
    if message.author.bot or not message.content:
        return
    if "NetflixId=" not in message.content:
        return
    if on_cooldown(message.author.id):
        return

    try:
        link = await asyncio.to_thread(generate_nftoken, build_cookie(message.content))
        await message.reply(link, mention_author=False)
    except Exception as exc:
        await message.reply(f"Failed: `{exc}`", mention_author=False)


@client.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print(f"Logged in as {client.user} | listening on port {PORT}", flush=True)


# ----------------------------------------------------------------------
# Boot: web server first (Render health check), then bot
# ----------------------------------------------------------------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN env var is missing")

    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()
    print(f"Web server bound to 0.0.0.0:{PORT}", flush=True)
    client.run(DISCORD_TOKEN)
