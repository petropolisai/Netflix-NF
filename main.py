import os
import re
import json
import time
import asyncio
import threading
from typing import Optional

import requests
from urllib3.exceptions import InsecureRequestWarning
from flask import Flask

import discord
from discord import app_commands

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# ======================================================================
# CONFIG  (Render environment variables)
# ======================================================================
DISCORD_TOKEN    = os.environ.get("DISCORD_TOKEN")
GUILD_ID         = os.environ.get("GUILD_ID")

# Multiple role IDs, comma separated:  "123456789,987654321,555555555"
ALLOWED_ROLE_IDS = [r.strip() for r in os.environ.get("ALLOWED_ROLE_IDS", "").split(",") if r.strip()]

# Admins skip the role check (set to "false" to require the role for everyone)
ADMIN_BYPASS     = os.environ.get("ADMIN_BYPASS", "true").lower() == "true"

PORT             = int(os.environ.get("PORT", 10000))
RENDER_URL       = os.environ.get("RENDER_EXTERNAL_URL")

TOOL_NAME = "MalixAris NFToken"
CREDIT    = "Made by AbubakarMalikGul"

COOLDOWN_SECONDS = 5
_last_use: dict[int, float] = {}

# ======================================================================
# Netflix logic  (same as MalixAris NFToken script)
# ======================================================================
API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"

QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "isTablet": "false",
    "languages": "en-US",
    "locale": "en-US",
    "maxDeviceWidth": "375",
    "model": "saget",
    "modelType": "IPHONE8-1",
    "odpAware": "true",
    "path": '["account","token","default"]',
    "pathFormat": "graph",
    "pixelDensity": "2.0",
    "progressive": "false",
    "responseFormat": "json",
}

BASE_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.attempt": "1",
    "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.argo.translated": "true",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.context.sdk-version": "2012.4",
    "x-netflix.client.appversion": "15.48.1",
    "x-netflix.context.max-device-width": "375",
    "x-netflix.context.ab-tests": "",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.argo.abtests": "",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}

REQUIRED_COOKIE = "NetflixId"

LINK_TEMPLATES = (
    ("PC", "https://netflix.com/?nftoken={token}"),
    ("MOBILE", "https://www.netflix.com/unsupported?nftoken={token}"),
    ("TV", "https://www.netflix.com/tv9?nftoken={token}"),
)


def _decode_cookie_value(value):
    if isinstance(value, str) and "%" in value:
        try:
            return requests.utils.unquote(value)
        except Exception:
            return value
    return value


def parse_netscape_cookie_line(line):
    parts = line.strip().split("\t")
    if len(parts) >= 7:
        return {parts[5]: parts[6]}
    return {}


def _add_cookie(cookie_dict, cookie):
    if not isinstance(cookie, dict):
        return
    name = cookie.get("name")
    value = cookie.get("value")
    if isinstance(name, str) and isinstance(value, str):
        cookie_dict[name] = _decode_cookie_value(value)


def extract_cookie_dict(text):
    """Capture EVERY cookie - raw paste, Netscape dump, or browser JSON export."""
    cookie_dict = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cookie_dict.update(parse_netscape_cookie_line(line))

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        for cookie in data:
            _add_cookie(cookie_dict, cookie)
    elif isinstance(data, dict):
        if isinstance(data.get("cookies"), list):
            for cookie in data["cookies"]:
                _add_cookie(cookie_dict, cookie)
        else:
            for name, value in data.items():
                if isinstance(value, str):
                    cookie_dict[name] = _decode_cookie_value(value)

    if not cookie_dict:
        flat = re.sub(r"^\s*cookie\s*:\s*", "", text, flags=re.I)
        flat = " ".join(flat.split())
        for name, value in re.findall(r"([^\s;=,]+)=([^;\s]+)", flat):
            cookie_dict[name] = _decode_cookie_value(value)

    return cookie_dict


def reorder_cookies(cookie_dict):
    if REQUIRED_COOKIE not in cookie_dict:
        return cookie_dict
    ordered = {REQUIRED_COOKIE: cookie_dict[REQUIRED_COOKIE]}
    for name, value in cookie_dict.items():
        if name != REQUIRED_COOKIE:
            ordered[name] = value
    return ordered


def build_cookie_header(cookie_dict):
    return "; ".join(f"{name}={value}" for name, value in cookie_dict.items())


def fetch_nftoken(cookie_dict):
    if REQUIRED_COOKIE not in cookie_dict:
        raise ValueError("Missing required cookie: " + REQUIRED_COOKIE)

    headers = dict(BASE_HEADERS)
    headers["Cookie"] = build_cookie_header(cookie_dict)

    response = requests.get(
        API_URL,
        params=QUERY_PARAMS,
        headers=headers,
        timeout=30,
        verify=False,
    )
    response.raise_for_status()

    data = response.json()
    token_data = (
        (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default")
        or {}
    )
    token = token_data.get("token")
    expires = token_data.get("expires")

    if not token:
        raise ValueError("No token found in response.")

    if isinstance(expires, int) and len(str(expires)) == 13:
        expires //= 1000

    return token, expires


def format_expiry(expires):
    from datetime import datetime
    if not isinstance(expires, (int, float)):
        return "Unknown"
    try:
        return datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(expires)


def generate_links(cookie_dict):
    token, expires = fetch_nftoken(cookie_dict)
    links = [(label, template.format(token=token)) for label, template in LINK_TEMPLATES]
    return links, format_expiry(expires)


# ======================================================================
# Keep-alive web server (Render needs a bound port)
# ======================================================================
web = Flask(__name__)


@web.get("/")
@web.get("/health")
def health():
    return "ok", 200


def run_web():
    web.run(host="0.0.0.0", port=PORT, use_reloader=False)


def self_ping():
    if not RENDER_URL:
        return
    while True:
        time.sleep(600)
        try:
            requests.get(RENDER_URL, timeout=10)
        except Exception:
            pass


# ======================================================================
# Discord bot
# ======================================================================
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def on_cooldown(user_id: int) -> int:
    now = time.time()
    last = _last_use.get(user_id, 0)
    if now - last < COOLDOWN_SECONDS:
        return int(COOLDOWN_SECONDS - (now - last)) + 1
    _last_use[user_id] = now
    return 0


def member_has_access(member) -> bool:
    if not ALLOWED_ROLE_IDS:
        return True
    if not isinstance(member, discord.Member):
        return True
    if ADMIN_BYPASS and member.guild_permissions.administrator:
        return True
    member_role_ids = {str(role.id) for role in member.roles}
    return any(role_id in member_role_ids for role_id in ALLOWED_ROLE_IDS)


async def deny(interaction: discord.Interaction):
    await interaction.response.send_message(
        "You need one of the allowed roles to use this command.",
        ephemeral=True,
    )


async def send_links(interaction: discord.Interaction, raw_cookie: str):
    cookie_dict = reorder_cookies(extract_cookie_dict(raw_cookie))

    if not cookie_dict:
        await interaction.followup.send("No valid cookie found in that.", ephemeral=True)
        return

    if REQUIRED_COOKIE not in cookie_dict:
        await interaction.followup.send(
            "Missing `NetflixId`. Paste ALL your cookies, not just one.",
            ephemeral=True,
        )
        return

    try:
        links, expires = await asyncio.to_thread(generate_links, cookie_dict)
    except Exception as exc:
        await interaction.followup.send(
            f"Failed: `{type(exc).__name__}: {exc}`", ephemeral=True
        )
        return

    embed = discord.Embed(
        title=TOOL_NAME,
        description="Netflix login links from your cookie.",
        color=0xE50914,
    )
    for label, link in links:
        embed.add_field(name=label, value=f"`{link}`", inline=False)
    embed.add_field(name="Expires", value=expires, inline=False)
    embed.set_footer(text=CREDIT)

    await interaction.followup.send(embed=embed, ephemeral=True)


# ----------------------------------------------------------------------
# /nftoken
# ----------------------------------------------------------------------
@tree.command(name="nftoken", description="Generate Netflix login links from a cookie")
@app_commands.describe(
    cookie="Paste ALL your cookies here (NetflixId=...; SecureNetflixId=...; ...)",
    file="...or attach input.txt / cookies.txt / JSON export",
)
async def nftoken(
    interaction: discord.Interaction,
    cookie: Optional[str] = None,
    file: Optional[discord.Attachment] = None,
):
    if not member_has_access(interaction.user):
        await deny(interaction)
        return

    await interaction.response.defer(ephemeral=True)

    wait = on_cooldown(interaction.user.id)
    if wait:
        await interaction.followup.send(f"Slow down — {wait}s.", ephemeral=True)
        return

    raw = cookie
    if file is not None:
        raw = (await file.read()).decode("utf-8", "ignore")

    if not raw:
        await interaction.followup.send(
            "Paste your cookies or attach a file.", ephemeral=True
        )
        return

    await send_links(interaction, raw)


# ----------------------------------------------------------------------
# /access  (tells you if you can use the bot)
# ----------------------------------------------------------------------
@tree.command(name="access", description="Check if you have access to this bot")
async def access(interaction: discord.Interaction):
    ok = member_has_access(interaction.user)
    await interaction.response.send_message(
        "You have access." if ok else "You do NOT have access.",
        ephemeral=True,
    )


# ----------------------------------------------------------------------
# Optional: paste a cookie in chat and get links
# ----------------------------------------------------------------------
@client.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.content:
        return
    if "NetflixId=" not in message.content:
        return
    if not member_has_access(message.author):
        return
    if on_cooldown(message.author.id):
        return

    try:
        links, expires = await asyncio.to_thread(
            generate_links, reorder_cookies(extract_cookie_dict(message.content))
        )
        body = "\n".join(f"**{label}:** {link}" for label, link in links)
        await message.reply(f"{body}\n**Expires:** {expires}", mention_author=False)
    except Exception as exc:
        await message.reply(f"Failed: `{exc}`", mention_author=False)


@client.event
async def on_ready():
    await client.change_presence(activity=discord.Game(name=TOOL_NAME))
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print(f"Logged in as {client.user} | port {PORT} | roles {ALLOWED_ROLE_IDS}", flush=True)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN env var is missing")

    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()
    print(f"Web bound to 0.0.0.0:{PORT}", flush=True)
    client.run(DISCORD_TOKEN)
