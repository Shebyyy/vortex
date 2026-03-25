import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp
from aiohttp import web
import asyncio
import os
import base64
import json
import re
import time
import datetime

# ── Config ─────────────────────────────────────────────────────────────────────

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN")
PORT          = int(os.environ.get("PORT", 8080))

GITHUB_OWNER  = "Shebyyy"
GITHUB_REPO   = "AnymeX-Preview"
GITHUB_BRANCH = "beta"
GITHUB_API    = "https://api.github.com"

# All data lives under this folder in the repo
DATA_ROOT = "vortex"

# ── File paths ─────────────────────────────────────────────────────────────────

FILE_CONFIG    = f"{DATA_ROOT}/config.json"       # per-guild settings
FILE_WARNINGS  = f"{DATA_ROOT}/warnings.json"     # warnings per user per guild
FILE_CASES     = f"{DATA_ROOT}/cases.json"        # mod case log
FILE_HONEYPOT  = f"{DATA_ROOT}/honeypot.json"     # honeypot channels
FILE_TICKETS   = f"{DATA_ROOT}/tickets.json"      # open tickets
FILE_RXROLES   = f"{DATA_ROOT}/rxroles.json"      # reaction roles
FILE_GIVEAWAYS = f"{DATA_ROOT}/giveaways.json"    # active giveaways
FILE_LEVELS    = f"{DATA_ROOT}/levels.json"       # XP per user per guild

# ── Defaults ───────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "mod_log": None,
    "welcome_channel": None,
    "welcome_message": "Welcome {user} to {server}! 🎉",
    "muted_role": None,
    "ticket_category": None,
    "ticket_log": None,
    "automod": {
        "spam":    {"enabled": False, "max_messages": 5, "interval": 5},
        "caps":    {"enabled": False, "threshold": 70},
        "links":   {"enabled": False, "whitelist": []},
        "words":   {"enabled": False, "blacklist": []},
        "invites": {"enabled": False},
        "mentions":{"enabled": False, "max": 5},
    },
    "logging": {
        "message_edit":   True,
        "message_delete": True,
        "member_join":    True,
        "member_leave":   True,
        "role_change":    True,
        "voice":          True,
    },
}

# ── Spam tracker (in-memory) ───────────────────────────────────────────────────

_spam_tracker: dict[str, dict[str, list]] = {}
_case_counter: dict[str, int] = {}

# ── GitHub helpers ─────────────────────────────────────────────────────────────

def gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

async def gh_read(session: aiohttp.ClientSession, filepath: str):
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filepath}?ref={GITHUB_BRANCH}"
    async with session.get(url, headers=gh_headers()) as r:
        if r.status == 404:
            return None, None
        data = await r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content), data["sha"]

async def gh_write(session: aiohttp.ClientSession, filepath: str, data, sha, msg: str):
    payload = {
        "message": msg,
        "content": base64.b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode(),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filepath}"
    async with session.put(url, headers=gh_headers(), json=payload) as r:
        return r.status in (200, 201)

# ── Config helpers ─────────────────────────────────────────────────────────────

async def get_config(session, guild_id: str) -> dict:
    all_cfg, _ = await gh_read(session, FILE_CONFIG)
    if not all_cfg:
        return DEFAULT_CONFIG.copy()
    return all_cfg.get(guild_id, DEFAULT_CONFIG.copy())

async def save_config(session, guild_id: str, cfg: dict):
    all_cfg, sha = await gh_read(session, FILE_CONFIG)
    if not all_cfg:
        all_cfg = {}
    all_cfg[guild_id] = cfg
    await gh_write(session, FILE_CONFIG, all_cfg, sha, f"Vortex: update config for {guild_id}")

async def get_warnings(session, guild_id: str) -> dict:
    all_w, _ = await gh_read(session, FILE_WARNINGS)
    if not all_w:
        return {}
    return all_w.get(guild_id, {})

async def save_warnings(session, guild_id: str, warnings: dict):
    all_w, sha = await gh_read(session, FILE_WARNINGS)
    if not all_w:
        all_w = {}
    all_w[guild_id] = warnings
    await gh_write(session, FILE_WARNINGS, all_w, sha, f"Vortex: update warnings for {guild_id}")

async def get_cases(session, guild_id: str) -> list:
    all_c, _ = await gh_read(session, FILE_CASES)
    if not all_c:
        return []
    return all_c.get(guild_id, [])

async def save_cases(session, guild_id: str, cases: list):
    all_c, sha = await gh_read(session, FILE_CASES)
    if not all_c:
        all_c = {}
    all_c[guild_id] = cases
    await gh_write(session, FILE_CASES, all_c, sha, f"Vortex: update cases for {guild_id}")

async def add_case(session, guild_id: str, action: str, mod: discord.Member, target, reason: str) -> int:
    cases = await get_cases(session, guild_id)
    case_id = len(cases) + 1
    cases.append({
        "id": case_id,
        "action": action,
        "mod_id": str(mod.id),
        "mod_name": str(mod),
        "target_id": str(target.id) if hasattr(target, "id") else str(target),
        "target_name": str(target),
        "reason": reason,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    })
    await save_cases(session, guild_id, cases)
    return case_id

async def get_levels(session, guild_id: str) -> dict:
    all_l, _ = await gh_read(session, FILE_LEVELS)
    if not all_l:
        return {}
    return all_l.get(guild_id, {})

async def save_levels(session, guild_id: str, levels: dict):
    all_l, sha = await gh_read(session, FILE_LEVELS)
    if not all_l:
        all_l = {}
    all_l[guild_id] = levels
    await gh_write(session, FILE_LEVELS, all_l, sha, f"Vortex: update levels for {guild_id}")

# ── Mod log helper ─────────────────────────────────────────────────────────────

async def send_mod_log(guild: discord.Guild, cfg: dict, embed: discord.Embed):
    ch_id = cfg.get("mod_log")
    if not ch_id:
        return
    ch = guild.get_channel(int(ch_id))
    if ch:
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

def mod_embed(color: int, title: str, fields: list[tuple], case_id: int = None) -> discord.Embed:
    e = discord.Embed(title=f"🌀 {title}", color=color, timestamp=datetime.datetime.utcnow())
    for name, value, inline in fields:
        e.add_field(name=name, value=value, inline=inline)
    if case_id:
        e.set_footer(text=f"Case #{case_id}")
    return e

# ── Health server ──────────────────────────────────────────────────────────────

async def health(request):
    return web.Response(text="🌀 Vortex is running!")

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"✅ Health server on port {PORT}")

# ── Bot setup ──────────────────────────────────────────────────────────────────

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="v!", intents=intents, help_command=None)

# ── Ensure data files exist ────────────────────────────────────────────────────

async def ensure_files():
    async with aiohttp.ClientSession() as session:
        for filepath, default in [
            (FILE_CONFIG,    {}),
            (FILE_WARNINGS,  {}),
            (FILE_CASES,     {}),
            (FILE_HONEYPOT,  {}),
            (FILE_TICKETS,   {}),
            (FILE_RXROLES,   {}),
            (FILE_GIVEAWAYS, {}),
            (FILE_LEVELS,    {}),
        ]:
            data, sha = await gh_read(session, filepath)
            if sha is None:
                await gh_write(session, filepath, default, None, f"Vortex: init {filepath}")
                print(f"✅ Created {filepath}")
            else:
                print(f"✅ {filepath} exists")

# ══════════════════════════════════════════════════════════════════════════════
# EVENTS
# ══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"🌀 Vortex online as {bot.user}")
    await ensure_files()
    if not check_giveaways.is_running():
        check_giveaways.start()
    if not getattr(bot, "_synced", False):
        try:
            await bot.tree.sync()
            bot._synced = True
            print("✅ Slash commands synced")
        except Exception as e:
            print(f"⚠️ Sync failed: {e}")


@bot.event
async def on_member_join(member: discord.Member):
    guild_id = str(member.guild.id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)

    # Welcome message
    ch_id = cfg.get("welcome_channel")
    if ch_id:
        ch = member.guild.get_channel(int(ch_id))
        if ch:
            msg = cfg.get("welcome_message", DEFAULT_CONFIG["welcome_message"])
            msg = msg.replace("{user}", member.mention).replace("{server}", member.guild.name)
            await ch.send(msg)

    # Log
    if cfg.get("logging", {}).get("member_join") and cfg.get("mod_log"):
        e = discord.Embed(title="🟢 Member joined", color=0x57F287, timestamp=datetime.datetime.utcnow())
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="User", value=f"{member} ({member.id})", inline=False)
        e.add_field(name="Account created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=False)
        await send_mod_log(member.guild, cfg, e)


@bot.event
async def on_member_remove(member: discord.Member):
    guild_id = str(member.guild.id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)
    if cfg.get("logging", {}).get("member_leave") and cfg.get("mod_log"):
        e = discord.Embed(title="🔴 Member left", color=0xED4245, timestamp=datetime.datetime.utcnow())
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="User", value=f"{member} ({member.id})", inline=False)
        await send_mod_log(member.guild, cfg, e)


@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    guild_id = str(message.guild.id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)
    if cfg.get("logging", {}).get("message_delete") and cfg.get("mod_log"):
        e = discord.Embed(title="🗑️ Message deleted", color=0xFEE75C, timestamp=datetime.datetime.utcnow())
        e.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=True)
        e.add_field(name="Channel", value=message.channel.mention, inline=True)
        e.add_field(name="Content", value=message.content[:1000] or "*empty*", inline=False)
        await send_mod_log(message.guild, cfg, e)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not before.guild or before.author.bot or before.content == after.content:
        return
    guild_id = str(before.guild.id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)
    if cfg.get("logging", {}).get("message_edit") and cfg.get("mod_log"):
        e = discord.Embed(title="✏️ Message edited", color=0x5865F2, timestamp=datetime.datetime.utcnow())
        e.add_field(name="Author", value=f"{before.author} ({before.author.id})", inline=True)
        e.add_field(name="Channel", value=before.channel.mention, inline=True)
        e.add_field(name="Before", value=before.content[:500] or "*empty*", inline=False)
        e.add_field(name="After", value=after.content[:500] or "*empty*", inline=False)
        await send_mod_log(before.guild, cfg, e)


@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    guild_id = str(member.guild.id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)
    if not cfg.get("logging", {}).get("voice") or not cfg.get("mod_log"):
        return
    if before.channel == after.channel:
        return
    if after.channel and not before.channel:
        desc = f"Joined **{after.channel.name}**"
        color = 0x57F287
    elif before.channel and not after.channel:
        desc = f"Left **{before.channel.name}**"
        color = 0xED4245
    else:
        desc = f"Moved **{before.channel.name}** → **{after.channel.name}**"
        color = 0xFEE75C
    e = discord.Embed(title="🔊 Voice update", description=desc, color=color, timestamp=datetime.datetime.utcnow())
    e.add_field(name="User", value=f"{member} ({member.id})", inline=False)
    await send_mod_log(member.guild, cfg, e)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles == after.roles:
        return
    guild_id = str(before.guild.id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)
    if not cfg.get("logging", {}).get("role_change") or not cfg.get("mod_log"):
        return
    added   = [r for r in after.roles  if r not in before.roles]
    removed = [r for r in before.roles if r not in after.roles]
    e = discord.Embed(title="🎭 Role update", color=0x9B59B6, timestamp=datetime.datetime.utcnow())
    e.add_field(name="User", value=f"{before} ({before.id})", inline=False)
    if added:
        e.add_field(name="Added", value=" ".join(r.mention for r in added), inline=True)
    if removed:
        e.add_field(name="Removed", value=" ".join(r.mention for r in removed), inline=True)
    await send_mod_log(before.guild, cfg, e)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if not payload.guild_id:
        return
    guild_id = str(payload.guild_id)
    async with aiohttp.ClientSession() as session:
        all_rx, _ = await gh_read(session, FILE_RXROLES)
    if not all_rx:
        return
    guild_rx = all_rx.get(guild_id, {})
    key = f"{payload.message_id}:{str(payload.emoji)}"
    role_id = guild_rx.get(key)
    if not role_id:
        return
    guild = bot.get_guild(payload.guild_id)
    role  = guild.get_role(int(role_id))
    member = guild.get_member(payload.user_id)
    if role and member and not member.bot:
        await member.add_roles(role, reason="Reaction role")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if not payload.guild_id:
        return
    guild_id = str(payload.guild_id)
    async with aiohttp.ClientSession() as session:
        all_rx, _ = await gh_read(session, FILE_RXROLES)
    if not all_rx:
        return
    guild_rx = all_rx.get(guild_id, {})
    key = f"{payload.message_id}:{str(payload.emoji)}"
    role_id = guild_rx.get(key)
    if not role_id:
        return
    guild  = bot.get_guild(payload.guild_id)
    role   = guild.get_role(int(role_id))
    member = guild.get_member(payload.user_id)
    if role and member and not member.bot:
        await member.remove_roles(role, reason="Reaction role removed")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    guild_id = str(message.guild.id)
    user     = message.author
    content  = message.content

    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)

    am = cfg.get("automod", DEFAULT_CONFIG["automod"])

    # ── Honeypot check ─────────────────────────────────────────────────────────
    async with aiohttp.ClientSession() as session:
        all_hp, _ = await gh_read(session, FILE_HONEYPOT)
    if all_hp:
        guild_hp = all_hp.get(guild_id, {})
        if str(message.channel.id) in guild_hp:
            try:
                await message.delete()
                await user.ban(reason="🌀 Vortex Honeypot triggered")
                e = discord.Embed(title="🍯 Honeypot triggered", color=0xFF0000, timestamp=datetime.datetime.utcnow())
                e.add_field(name="User", value=f"{user} ({user.id})", inline=True)
                e.add_field(name="Channel", value=message.channel.mention, inline=True)
                await send_mod_log(message.guild, cfg, e)
            except Exception:
                pass
            return

    # ── XP / leveling ──────────────────────────────────────────────────────────
    async with aiohttp.ClientSession() as session:
        levels = await get_levels(session, guild_id)
        uid = str(user.id)
        entry = levels.get(uid, {"xp": 0, "level": 0, "last_msg": 0})
        now = time.time()
        if now - entry.get("last_msg", 0) > 60:
            entry["xp"] += 15
            entry["last_msg"] = now
            xp_needed = (entry["level"] + 1) * 100
            if entry["xp"] >= xp_needed:
                entry["level"] += 1
                entry["xp"]    -= xp_needed
                await message.channel.send(
                    f"🎉 {user.mention} reached **level {entry['level']}**!", delete_after=10
                )
            levels[uid] = entry
            await save_levels(session, guild_id, levels)

    async def automod_action(action: str, reason: str):
        try:
            await message.delete()
        except Exception:
            pass
        muted_role_id = cfg.get("muted_role")
        if action == "delete":
            pass
        elif action == "warn":
            await message.channel.send(f"⚠️ {user.mention} — {reason}", delete_after=5)
        elif action == "mute" and muted_role_id:
            role = message.guild.get_role(int(muted_role_id))
            if role:
                await user.add_roles(role, reason=f"Automod: {reason}")
        elif action == "kick":
            await user.kick(reason=f"Automod: {reason}")
        elif action == "ban":
            await user.ban(reason=f"Automod: {reason}")
        e = discord.Embed(title=f"🤖 Automod: {reason}", color=0xFF6B35, timestamp=datetime.datetime.utcnow())
        e.add_field(name="User",    value=f"{user} ({user.id})", inline=True)
        e.add_field(name="Channel", value=message.channel.mention, inline=True)
        e.add_field(name="Action",  value=action, inline=True)
        await send_mod_log(message.guild, cfg, e)

    # Invites
    if am.get("invites", {}).get("enabled") and re.search(r"discord\.gg/|discord\.com/invite/", content, re.I):
        await automod_action("delete", "Invite link")

    # Caps
    elif am.get("caps", {}).get("enabled") and len(content) > 8:
        caps_pct = sum(1 for c in content if c.isupper()) / max(len(content), 1) * 100
        if caps_pct >= am["caps"].get("threshold", 70):
            await automod_action("delete", "Excessive caps")

    # Mentions
    elif am.get("mentions", {}).get("enabled") and len(message.mentions) >= am["mentions"].get("max", 5):
        await automod_action("mute", "Mention spam")

    # Word blacklist
    elif am.get("words", {}).get("enabled"):
        lower = content.lower()
        if any(w in lower for w in am["words"].get("blacklist", [])):
            await automod_action("delete", "Blacklisted word")

    # Link filter
    elif am.get("links", {}).get("enabled"):
        urls = re.findall(r"https?://\S+", content)
        whitelist = am["links"].get("whitelist", [])
        if urls and not all(any(w in u for w in whitelist) for u in urls):
            await automod_action("delete", "Blocked URL")

    # Spam
    elif am.get("spam", {}).get("enabled"):
        now_ts = time.time()
        if guild_id not in _spam_tracker:
            _spam_tracker[guild_id] = {}
        stamps = _spam_tracker[guild_id].get(str(user.id), [])
        interval = am["spam"].get("interval", 5)
        stamps = [t for t in stamps if now_ts - t < interval]
        stamps.append(now_ts)
        _spam_tracker[guild_id][str(user.id)] = stamps
        if len(stamps) >= am["spam"].get("max_messages", 5):
            await automod_action("mute", "Spam")

    await bot.process_commands(message)


# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

def is_mod():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.guild_permissions.moderate_members
    return app_commands.check(predicate)

def is_admin():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)


@bot.tree.command(name="ban", description="Ban a member")
@app_commands.describe(member="Member to ban", reason="Reason", delete_days="Days of messages to delete")
@is_mod()
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason", delete_days: int = 0):
    await interaction.response.defer(ephemeral=True)
    try:
        await member.send(f"❌ You have been **banned** from **{interaction.guild.name}**.\nReason: {reason}")
    except Exception:
        pass
    await member.ban(reason=reason, delete_message_days=delete_days)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, str(interaction.guild_id))
        case_id = await add_case(session, str(interaction.guild_id), "ban", interaction.user, member, reason)
    e = mod_embed(0xED4245, "Member banned", [
        ("User", f"{member} ({member.id})", True),
        ("Mod",  f"{interaction.user}", True),
        ("Reason", reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"✅ Banned **{member}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="unban", description="Unban a user by ID")
@app_commands.describe(user_id="User ID to unban", reason="Reason")
@is_mod()
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        async with aiohttp.ClientSession() as session:
            cfg  = await get_config(session, str(interaction.guild_id))
            case_id = await add_case(session, str(interaction.guild_id), "unban", interaction.user, user, reason)
        e = mod_embed(0x57F287, "Member unbanned", [
            ("User", f"{user} ({user.id})", True),
            ("Mod",  f"{interaction.user}", True),
            ("Reason", reason, False),
        ], case_id)
        await send_mod_log(interaction.guild, cfg, e)
        await interaction.followup.send(f"✅ Unbanned **{user}** | Case #{case_id}", ephemeral=True)
    except Exception as ex:
        await interaction.followup.send(f"❌ Failed: {ex}", ephemeral=True)


@bot.tree.command(name="kick", description="Kick a member")
@app_commands.describe(member="Member to kick", reason="Reason")
@is_mod()
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    try:
        await member.send(f"👢 You have been **kicked** from **{interaction.guild.name}**.\nReason: {reason}")
    except Exception:
        pass
    await member.kick(reason=reason)
    async with aiohttp.ClientSession() as session:
        cfg     = await get_config(session, str(interaction.guild_id))
        case_id = await add_case(session, str(interaction.guild_id), "kick", interaction.user, member, reason)
    e = mod_embed(0xFEE75C, "Member kicked", [
        ("User", f"{member} ({member.id})", True),
        ("Mod",  f"{interaction.user}", True),
        ("Reason", reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"✅ Kicked **{member}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="mute", description="Timeout a member")
@app_commands.describe(member="Member to mute", duration="Duration in minutes", reason="Reason")
@is_mod()
async def mute(interaction: discord.Interaction, member: discord.Member, duration: int = 10, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    until = discord.utils.utcnow() + datetime.timedelta(minutes=duration)
    await member.timeout(until, reason=reason)
    async with aiohttp.ClientSession() as session:
        cfg     = await get_config(session, str(interaction.guild_id))
        case_id = await add_case(session, str(interaction.guild_id), "mute", interaction.user, member, reason)
    e = mod_embed(0x9B59B6, "Member muted", [
        ("User",     f"{member} ({member.id})", True),
        ("Mod",      f"{interaction.user}", True),
        ("Duration", f"{duration} minutes", True),
        ("Reason",   reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"✅ Muted **{member}** for {duration}m | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="unmute", description="Remove timeout from a member")
@app_commands.describe(member="Member to unmute", reason="Reason")
@is_mod()
async def unmute(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    await member.timeout(None, reason=reason)
    async with aiohttp.ClientSession() as session:
        cfg     = await get_config(session, str(interaction.guild_id))
        case_id = await add_case(session, str(interaction.guild_id), "unmute", interaction.user, member, reason)
    e = mod_embed(0x57F287, "Member unmuted", [
        ("User", f"{member} ({member.id})", True),
        ("Mod",  f"{interaction.user}", True),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"✅ Unmuted **{member}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="warn", description="Warn a member")
@app_commands.describe(member="Member to warn", reason="Reason")
@is_mod()
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        cfg      = await get_config(session, guild_id)
        warnings = await get_warnings(session, guild_id)
        uid      = str(member.id)
        if uid not in warnings:
            warnings[uid] = []
        warnings[uid].append({
            "reason": reason,
            "mod": str(interaction.user),
            "timestamp": datetime.datetime.utcnow().isoformat(),
        })
        await save_warnings(session, guild_id, warnings)
        case_id = await add_case(session, guild_id, "warn", interaction.user, member, reason)
    count = len(warnings[uid])
    try:
        await member.send(f"⚠️ You have been warned in **{interaction.guild.name}**.\nReason: {reason}\nTotal warnings: {count}")
    except Exception:
        pass
    e = mod_embed(0xFEE75C, "Member warned", [
        ("User",     f"{member} ({member.id})", True),
        ("Mod",      f"{interaction.user}", True),
        ("Warnings", str(count), True),
        ("Reason",   reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"⚠️ Warned **{member}** (Warning #{count}) | Case #{case_id}", ephemeral=True)

    # Auto-punish at thresholds
    if count >= 5:
        await member.ban(reason="5 warnings accumulated")
        await interaction.channel.send(f"🔨 **{member}** has been auto-banned for reaching 5 warnings.", delete_after=10)
    elif count >= 3:
        until = discord.utils.utcnow() + datetime.timedelta(hours=1)
        await member.timeout(until, reason="3 warnings accumulated")
        await interaction.channel.send(f"🔇 **{member}** has been auto-muted (1h) for reaching 3 warnings.", delete_after=10)


@bot.tree.command(name="warnings", description="View warnings for a member")
@app_commands.describe(member="Member to check")
@is_mod()
async def warnings(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        warns = await get_warnings(session, str(interaction.guild_id))
    user_warns = warns.get(str(member.id), [])
    e = discord.Embed(title=f"⚠️ Warnings for {member}", color=0xFEE75C)
    if not user_warns:
        e.description = "No warnings."
    for i, w in enumerate(user_warns[-10:], 1):
        e.add_field(name=f"#{i} — {w['mod']}", value=f"{w['reason']}\n<t:{int(datetime.datetime.fromisoformat(w['timestamp']).timestamp())}:R>", inline=False)
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="clearwarnings", description="Clear all warnings for a member")
@app_commands.describe(member="Member to clear warnings for")
@is_admin()
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        warns = await get_warnings(session, guild_id)
        warns[str(member.id)] = []
        await save_warnings(session, guild_id, warns)
    await interaction.followup.send(f"✅ Cleared warnings for **{member}**", ephemeral=True)


@bot.tree.command(name="purge", description="Delete messages in bulk")
@app_commands.describe(amount="Number of messages to delete (max 100)")
@is_mod()
async def purge(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)
    amount = min(amount, 100)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)


@bot.tree.command(name="cases", description="View recent mod cases")
@is_mod()
async def cases(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        all_cases = await get_cases(session, str(interaction.guild_id))
    recent = all_cases[-10:]
    e = discord.Embed(title="📋 Recent mod cases", color=0x5865F2)
    if not recent:
        e.description = "No cases yet."
    for c in reversed(recent):
        e.add_field(
            name=f"#{c['id']} — {c['action'].upper()} by {c['mod_name']}",
            value=f"**Target:** {c['target_name']}\n**Reason:** {c['reason']}",
            inline=False,
        )
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="userinfo", description="View info about a user")
@app_commands.describe(member="Member to inspect")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    e = discord.Embed(title=f"👤 {member}", color=member.color)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="ID",      value=member.id, inline=True)
    e.add_field(name="Joined",  value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
    e.add_field(name="Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    e.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:10]) or "None", inline=False)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="serverinfo", description="View server info")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    e = discord.Embed(title=f"🏠 {g.name}", color=0x5865F2)
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    e.add_field(name="Owner",    value=g.owner.mention if g.owner else "Unknown", inline=True)
    e.add_field(name="Members",  value=g.member_count, inline=True)
    e.add_field(name="Channels", value=len(g.channels), inline=True)
    e.add_field(name="Roles",    value=len(g.roles), inline=True)
    e.add_field(name="Created",  value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
    e.add_field(name="Boost lvl", value=g.premium_tier, inline=True)
    await interaction.response.send_message(embed=e)


# ══════════════════════════════════════════════════════════════════════════════
# HONEYPOT
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="honeypot_add", description="Mark a channel as a honeypot trap")
@app_commands.describe(channel="Channel to mark as honeypot")
@is_admin()
async def honeypot_add(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        all_hp, sha = await gh_read(session, FILE_HONEYPOT)
        if not all_hp:
            all_hp = {}
        if guild_id not in all_hp:
            all_hp[guild_id] = {}
        all_hp[guild_id][str(channel.id)] = True
        await gh_write(session, FILE_HONEYPOT, all_hp, sha, f"Vortex: add honeypot {channel.id}")
    await interaction.followup.send(f"🍯 **{channel.name}** is now a honeypot. Anyone who sends there gets auto-banned!", ephemeral=True)


@bot.tree.command(name="honeypot_remove", description="Remove a honeypot channel")
@app_commands.describe(channel="Channel to remove from honeypots")
@is_admin()
async def honeypot_remove(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        all_hp, sha = await gh_read(session, FILE_HONEYPOT)
        if all_hp and guild_id in all_hp:
            all_hp[guild_id].pop(str(channel.id), None)
            await gh_write(session, FILE_HONEYPOT, all_hp, sha, f"Vortex: remove honeypot {channel.id}")
    await interaction.followup.send(f"✅ **{channel.name}** is no longer a honeypot.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# TICKETS
# ══════════════════════════════════════════════════════════════════════════════

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="vortex:close_ticket")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message("Only mods can close tickets.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Closing ticket...")
        await asyncio.sleep(3)
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")


class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, emoji="🎟️", custom_id="vortex:open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild    = interaction.guild
        guild_id = str(guild.id)
        async with aiohttp.ClientSession() as session:
            cfg = await get_config(session, guild_id)
        cat_id = cfg.get("ticket_category")
        category = guild.get_channel(int(cat_id)) if cat_id else None

        # Check if user already has a ticket
        existing = discord.utils.get(guild.text_channels, name=f"ticket-{interaction.user.name.lower()}")
        if existing:
            await interaction.followup.send(f"You already have an open ticket: {existing.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user:   discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        # Add mod roles
        for role in guild.roles:
            if role.permissions.moderate_members:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        ch = await guild.create_text_channel(
            f"ticket-{interaction.user.name}",
            category=category,
            overwrites=overwrites,
            reason=f"Ticket opened by {interaction.user}",
        )
        e = discord.Embed(
            title="🎟️ Support Ticket",
            description=f"Hello {interaction.user.mention}! A mod will be with you shortly.\nDescribe your issue below.",
            color=0x5865F2,
        )
        await ch.send(embed=e, view=TicketCloseView())
        await interaction.followup.send(f"✅ Ticket opened: {ch.mention}", ephemeral=True)


@bot.tree.command(name="ticket_setup", description="Post the ticket open panel")
@app_commands.describe(channel="Channel to post the panel in")
@is_admin()
async def ticket_setup(interaction: discord.Interaction, channel: discord.TextChannel):
    e = discord.Embed(
        title="🎟️ Support Tickets",
        description="Click the button below to open a private support ticket with the mod team.",
        color=0x5865F2,
    )
    await channel.send(embed=e, view=TicketOpenView())
    await interaction.response.send_message(f"✅ Ticket panel posted in {channel.mention}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# REACTION ROLES
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="rxrole_add", description="Add a reaction role to a message")
@app_commands.describe(message_id="Message ID", emoji="Emoji to react with", role="Role to assign")
@is_admin()
async def rxrole_add(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        all_rx, sha = await gh_read(session, FILE_RXROLES)
        if not all_rx:
            all_rx = {}
        if guild_id not in all_rx:
            all_rx[guild_id] = {}
        key = f"{message_id}:{emoji}"
        all_rx[guild_id][key] = str(role.id)
        await gh_write(session, FILE_RXROLES, all_rx, sha, f"Vortex: add rxrole {key}")
    try:
        msg = await interaction.channel.fetch_message(int(message_id))
        await msg.add_reaction(emoji)
    except Exception:
        pass
    await interaction.followup.send(f"✅ Reaction role set: {emoji} → {role.mention}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# GIVEAWAYS
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="giveaway", description="Start a giveaway")
@app_commands.describe(channel="Channel", duration="Duration in minutes", winners="Number of winners", prize="Prize")
@is_mod()
async def giveaway(interaction: discord.Interaction, channel: discord.TextChannel, duration: int, winners: int = 1, prize: str = "Mystery prize"):
    await interaction.response.defer(ephemeral=True)
    ends_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=duration)
    e = discord.Embed(
        title="🎉 GIVEAWAY",
        description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{int(ends_at.timestamp())}:R>\n\nReact with 🎉 to enter!",
        color=0xFF73FA,
    )
    msg = await channel.send(embed=e)
    await msg.add_reaction("🎉")
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        all_g, sha = await gh_read(session, FILE_GIVEAWAYS)
        if not all_g:
            all_g = {}
        if guild_id not in all_g:
            all_g[guild_id] = []
        all_g[guild_id].append({
            "message_id":  str(msg.id),
            "channel_id":  str(channel.id),
            "prize":       prize,
            "winners":     winners,
            "ends_at":     ends_at.isoformat(),
            "ended":       False,
        })
        await gh_write(session, FILE_GIVEAWAYS, all_g, sha, "Vortex: new giveaway")
    await interaction.followup.send(f"✅ Giveaway started in {channel.mention}!", ephemeral=True)


@tasks.loop(minutes=1)
async def check_giveaways():
    now = datetime.datetime.utcnow()
    async with aiohttp.ClientSession() as session:
        all_g, sha = await gh_read(session, FILE_GIVEAWAYS)
        if not all_g:
            return
        changed = False
        for guild_id, gaws in all_g.items():
            for gw in gaws:
                if gw.get("ended"):
                    continue
                ends_at = datetime.datetime.fromisoformat(gw["ends_at"])
                if now >= ends_at:
                    gw["ended"] = True
                    changed = True
                    guild   = bot.get_guild(int(guild_id))
                    if not guild:
                        continue
                    ch  = guild.get_channel(int(gw["channel_id"]))
                    if not ch:
                        continue
                    try:
                        msg = await ch.fetch_message(int(gw["message_id"]))
                        reaction = discord.utils.get(msg.reactions, emoji="🎉")
                        if reaction:
                            users = [u async for u in reaction.users() if not u.bot]
                            import random
                            if users:
                                chosen = random.sample(users, min(gw["winners"], len(users)))
                                winners_text = " ".join(u.mention for u in chosen)
                                await ch.send(f"🎉 Giveaway ended! Winners: {winners_text}\nPrize: **{gw['prize']}**")
                            else:
                                await ch.send("🎉 Giveaway ended but no one entered.")
                    except Exception:
                        pass
        if changed:
            await gh_write(session, FILE_GIVEAWAYS, all_g, sha, "Vortex: end giveaway")


# ══════════════════════════════════════════════════════════════════════════════
# LEVELING
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="rank", description="Check your rank")
@app_commands.describe(member="Member to check (default: yourself)")
async def rank(interaction: discord.Interaction, member: discord.Member = None):
    member   = member or interaction.user
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        levels = await get_levels(session, guild_id)
    entry = levels.get(str(member.id), {"xp": 0, "level": 0})
    xp_needed = (entry["level"] + 1) * 100
    e = discord.Embed(title=f"⭐ {member.display_name}'s rank", color=0xFFD700)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="Level", value=entry["level"], inline=True)
    e.add_field(name="XP",    value=f"{entry['xp']}/{xp_needed}", inline=True)
    sorted_users = sorted(levels.items(), key=lambda x: (x[1].get("level", 0), x[1].get("xp", 0)), reverse=True)
    rank_pos = next((i+1 for i, (uid, _) in enumerate(sorted_users) if uid == str(member.id)), "?")
    e.add_field(name="Rank", value=f"#{rank_pos}", inline=True)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="leaderboard", description="View the XP leaderboard")
async def leaderboard(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        levels = await get_levels(session, guild_id)
    sorted_users = sorted(levels.items(), key=lambda x: (x[1].get("level", 0), x[1].get("xp", 0)), reverse=True)[:10]
    e = discord.Embed(title="🏆 XP Leaderboard", color=0xFFD700)
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, data) in enumerate(sorted_users):
        medal = medals[i] if i < 3 else f"**#{i+1}**"
        member = interaction.guild.get_member(int(uid))
        name   = member.display_name if member else f"User {uid}"
        e.add_field(name=f"{medal} {name}", value=f"Level {data.get('level', 0)} — {data.get('xp', 0)} XP", inline=False)
    await interaction.response.send_message(embed=e)


# ══════════════════════════════════════════════════════════════════════════════
# POLLS
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="poll", description="Create a poll")
@app_commands.describe(question="Poll question", option1="Option 1", option2="Option 2", option3="Option 3 (optional)", option4="Option 4 (optional)")
async def poll(interaction: discord.Interaction, question: str, option1: str, option2: str, option3: str = None, option4: str = None):
    options = [o for o in [option1, option2, option3, option4] if o]
    emojis  = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    e = discord.Embed(title=f"📊 {question}", color=0x5865F2)
    for i, opt in enumerate(options):
        e.add_field(name=f"{emojis[i]} {opt}", value="\u200b", inline=False)
    e.set_footer(text=f"Poll by {interaction.user.display_name}")
    await interaction.response.send_message(embed=e)
    msg = await interaction.original_response()
    for i in range(len(options)):
        await msg.add_reaction(emojis[i])


# ══════════════════════════════════════════════════════════════════════════════
# SETUP COMMANDS (admin)
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="setup_modlog", description="Set the mod log channel")
@app_commands.describe(channel="Channel for mod logs")
@is_admin()
async def setup_modlog(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)
        cfg["mod_log"] = str(channel.id)
        await save_config(session, guild_id, cfg)
    await interaction.followup.send(f"✅ Mod log set to {channel.mention}", ephemeral=True)


@bot.tree.command(name="setup_welcome", description="Set the welcome channel and message")
@app_commands.describe(channel="Welcome channel", message="Welcome message ({user} and {server} are placeholders)")
@is_admin()
async def setup_welcome(interaction: discord.Interaction, channel: discord.TextChannel, message: str = None):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)
        cfg["welcome_channel"] = str(channel.id)
        if message:
            cfg["welcome_message"] = message
        await save_config(session, guild_id, cfg)
    await interaction.followup.send(f"✅ Welcome channel set to {channel.mention}", ephemeral=True)


@bot.tree.command(name="setup_tickets", description="Set the ticket category")
@app_commands.describe(category="Category for ticket channels")
@is_admin()
async def setup_tickets(interaction: discord.Interaction, category: discord.CategoryChannel):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)
        cfg["ticket_category"] = str(category.id)
        await save_config(session, guild_id, cfg)
    await interaction.followup.send(f"✅ Ticket category set to **{category.name}**", ephemeral=True)


@bot.tree.command(name="automod_setup", description="Configure automod rules")
@app_commands.describe(
    rule="Rule to configure",
    enabled="Enable or disable",
    action="Action (delete/warn/mute/kick/ban)",
)
@app_commands.choices(rule=[
    app_commands.Choice(name="spam",    value="spam"),
    app_commands.Choice(name="caps",    value="caps"),
    app_commands.Choice(name="links",   value="links"),
    app_commands.Choice(name="words",   value="words"),
    app_commands.Choice(name="invites", value="invites"),
    app_commands.Choice(name="mentions",value="mentions"),
])
@is_admin()
async def automod_setup(interaction: discord.Interaction, rule: str, enabled: bool, action: str = "delete"):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)
        if "automod" not in cfg:
            cfg["automod"] = DEFAULT_CONFIG["automod"].copy()
        if rule not in cfg["automod"]:
            cfg["automod"][rule] = {}
        cfg["automod"][rule]["enabled"] = enabled
        cfg["automod"][rule]["action"]  = action
        await save_config(session, guild_id, cfg)
    status = "enabled ✅" if enabled else "disabled ❌"
    await interaction.followup.send(f"Automod **{rule}** is now {status} (action: `{action}`)", ephemeral=True)


@bot.tree.command(name="automod_words", description="Add/remove words from the blacklist")
@app_commands.describe(action="add or remove", word="Word to add or remove")
@app_commands.choices(action=[
    app_commands.Choice(name="add",    value="add"),
    app_commands.Choice(name="remove", value="remove"),
])
@is_admin()
async def automod_words(interaction: discord.Interaction, action: str, word: str):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, guild_id)
        if "automod" not in cfg:
            cfg["automod"] = DEFAULT_CONFIG["automod"].copy()
        words = cfg["automod"].setdefault("words", {}).setdefault("blacklist", [])
        if action == "add" and word not in words:
            words.append(word.lower())
        elif action == "remove" and word.lower() in words:
            words.remove(word.lower())
        await save_config(session, guild_id, cfg)
    await interaction.followup.send(f"✅ Word `{word}` {action}ed to blacklist.", ephemeral=True)


@bot.tree.command(name="vortex", description="Show Vortex bot info")
async def vortex_info(interaction: discord.Interaction):
    e = discord.Embed(
        title="🌀 Vortex",
        description="A powerful moderation & community bot.",
        color=0x5865F2,
    )
    e.add_field(name="Moderation",  value="ban, kick, mute, warn, purge", inline=True)
    e.add_field(name="Automod",     value="spam, caps, links, words, invites, mentions", inline=True)
    e.add_field(name="Honeypot",    value="auto-ban trap channels", inline=True)
    e.add_field(name="Tickets",     value="private support channels", inline=True)
    e.add_field(name="Leveling",    value="XP, levels, leaderboard", inline=True)
    e.add_field(name="Giveaways",   value="timed giveaways with auto-winners", inline=True)
    e.add_field(name="Reaction roles", value="emoji → role assignment", inline=True)
    e.add_field(name="Logging",     value="messages, joins, voice, roles", inline=True)
    e.add_field(name="Polls",       value="up to 4 options", inline=True)
    e.set_footer(text="Use /setup_modlog to get started!")
    await interaction.response.send_message(embed=e)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    await start_health_server()
    _proxy_host = os.environ.get("PROXY_HOST")
    _proxy_port = os.environ.get("PROXY_PORT")
    _proxy_user = os.environ.get("PROXY_USER")
    _proxy_pass = os.environ.get("PROXY_PASS")
    proxy_url = (
        f"http://{_proxy_user}:{_proxy_pass}@{_proxy_host}:{_proxy_port}"
        if all([_proxy_host, _proxy_port, _proxy_user, _proxy_pass])
        else None
    )
    if proxy_url:
        print(f"✅ Using proxy: {_proxy_host}:{_proxy_port}")
        bot.http.proxy = proxy_url
    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
