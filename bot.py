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
import random
import unicodedata
from typing import Optional, Union

# ── Config ─────────────────────────────────────────────────────────────────────

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN")
PORT          = int(os.environ.get("PORT", 8080))

GITHUB_OWNER  = "Shebyyy"
GITHUB_REPO   = "vortex-db"
GITHUB_API    = "https://api.github.com"

# Each guild gets its own branch: "{guild_id}-{guild_name}"
# Files are flat JSON at root of each branch (no subfolders needed)

# ── File names (same on every branch) ─────────────────────────────────────────

FILE_CONFIG      = "config.json"
FILE_WARNINGS    = "warnings.json"
FILE_CASES       = "cases.json"
FILE_HONEYPOT    = "honeypot.json"
FILE_TICKETS     = "tickets.json"
FILE_RXROLES     = "rxroles.json"
FILE_GIVEAWAYS   = "giveaways.json"
FILE_LEVELS      = "levels.json"
FILE_TEMPACTIONS = "tempactions.json"
FILE_RAIDMODE    = "raidmode.json"
FILE_MODROLES    = "modroles.json"
FILE_LOCKED      = "locked.json"

# ── Defaults ───────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "mod_log": None,
    "welcome_channel": None,
    "welcome_message": "Welcome {user} to {server}! 🎉",
    "muted_role": None,
    "quarantine_role": None,
    "verified_role": None,
    "ticket_category": None,
    "ticket_log": None,
    "mod_roles": [],
    "admin_roles": [],
    "honeypot_protected_roles": [],
    "automod": {
        "spam":    {"enabled": False, "max_messages": 5, "interval": 5},
        "caps":    {"enabled": False, "threshold": 70},
        "links":   {"enabled": False, "whitelist": []},
        "words":   {"enabled": False, "blacklist": []},
        "invites": {"enabled": False},
        "mentions":{"enabled": False, "max": 5},
        "emojis":  {"enabled": False, "max": 10},
        "newlines":{"enabled": False, "max": 10},
        "zalgo":   {"enabled": False},
    },
    "logging": {
        "message_edit":   True,
        "message_delete": True,
        "member_join":    True,
        "member_leave":   True,
        "role_change":    True,
        "voice":          True,
        "nickname":       True,
        "ban":            True,
        "unban":          True,
        "kick":           True,
        "channel":        True,
        "emoji":          True,
        "invite":         True,
    },
    "warning_punishments": {
        "3": "mute",
        "5": "kick",
        "7": "ban"
    },
    "raid_mode": False,
    "raid_threshold": 10,
    "raid_interval": 30,
    "min_account_age": 0,
}

# ── In-memory trackers ─────────────────────────────────────────────────────────

_spam_tracker: dict[str, dict[str, list]] = {}
_case_counter: dict[str, int] = {}
_join_tracker: dict[str, list] = {}
_ghost_pings: dict[str, list] = {}

# ── Utility Functions ──────────────────────────────────────────────────────────

def parse_duration(duration_str: str) -> Optional[datetime.timedelta]:
    """Parse duration string like 1h, 30m, 1d, 1w into timedelta"""
    if not duration_str:
        return None
    duration_str = duration_str.lower().strip()
    
    # Handle permanent
    if duration_str in ('perm', 'permanent', '0', '0s', '0m', '0h', '0d'):
        return None
    
    # Pattern matching
    patterns = {
        's': ('seconds', r'(\d+)s(?:ec(?:onds?)?)?'),
        'm': ('minutes', r'(\d+)m(?:in(?:utes?)?)?'),
        'h': ('hours', r'(\d+)h(?:r(?:s?)?|ours?)?'),
        'd': ('days', r'(\d+)d(?:ays?)?'),
        'w': ('weeks', r'(\d+)w(?:ks?|eeks?)?'),
    }
    
    kwargs = {}
    for unit, (key, pattern) in patterns.items():
        match = re.search(pattern, duration_str)
        if match:
            kwargs[key] = int(match.group(1))
    
    if not kwargs:
        # Try to parse as just a number (assume minutes)
        try:
            minutes = int(duration_str)
            kwargs['minutes'] = minutes
        except ValueError:
            return None
    
    return datetime.timedelta(**kwargs)


def format_timedelta(td: datetime.timedelta) -> str:
    """Format timedelta to human readable string"""
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return "expired"
    
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{seconds}s")
    
    return " ".join(parts)


def is_hoisting(name: str) -> bool:
    """Check if name starts with hoisting characters"""
    if not name:
        return False
    first_char = name[0]
    hoist_chars = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    return first_char in hoist_chars or unicodedata.category(first_char) in ('So', 'Sk')


def contains_zalgo(text: str) -> bool:
    """Check for excessive combining characters (zalgo)"""
    combining_count = sum(1 for c in text if unicodedata.category(c) == 'Mn')
    return combining_count > len(text) * 0.3


# ── GitHub helpers ─────────────────────────────────────────────────────────────

def gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

# ── Branch management ──────────────────────────────────────────────────────────

def guild_branch(guild: discord.Guild) -> str:
    """Return the branch name for a guild: {id}-{sanitized-name}"""
    safe_name = re.sub(r"[^a-zA-Z0-9\-]", "-", guild.name).strip("-").lower()
    safe_name = re.sub(r"-+", "-", safe_name)[:40]
    return f"{guild.id}-{safe_name}"

def guild_branch_from_id(guild_id: str) -> str:
    """Look up the branch name by guild_id string (uses bot cache)."""
    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if guild:
        return guild_branch(guild)
    return str(guild_id)  # fallback: bare id

async def ensure_guild_branch(session: aiohttp.ClientSession, branch: str):
    """Create the guild branch from main if it doesn't exist yet."""
    # Check if branch exists
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/branches/{branch}"
    async with session.get(url, headers=gh_headers()) as r:
        if r.status == 200:
            return  # Already exists

    # Get SHA of main branch to branch from
    async with session.get(
        f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/refs/heads/main",
        headers=gh_headers()
    ) as r:
        if r.status != 200:
            # Try master if main doesn't exist
            async with session.get(
                f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/refs/heads/master",
                headers=gh_headers()
            ) as r2:
                if r2.status != 200:
                    return
                data = await r2.json()
        else:
            data = await r.json()
    sha = data["object"]["sha"]

    # Create the branch
    payload = {"ref": f"refs/heads/{branch}", "sha": sha}
    async with session.post(
        f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/refs",
        headers=gh_headers(),
        json=payload
    ) as r:
        if r.status in (200, 201):
            print(f"✅ Created branch: {branch}")

async def gh_read(session: aiohttp.ClientSession, filepath: str, branch: str):
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filepath}?ref={branch}"
    async with session.get(url, headers=gh_headers()) as r:
        if r.status == 404:
            return None, None
        data = await r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content), data["sha"]

async def gh_write(session: aiohttp.ClientSession, filepath: str, data, sha, msg: str, branch: str):
    payload = {
        "message": msg,
        "content": base64.b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode(),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filepath}"
    async with session.put(url, headers=gh_headers(), json=payload) as r:
        return r.status in (200, 201)

# ── Per-guild data helpers ─────────────────────────────────────────────────────
# Each guild has its own branch. Files are flat JSON (no guild_id key needed).

async def get_config(session, guild: discord.Guild) -> dict:
    branch = guild_branch(guild)
    data, _ = await gh_read(session, FILE_CONFIG, branch)
    if not data:
        return DEFAULT_CONFIG.copy()
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)
    return cfg

async def save_config(session, guild: discord.Guild, cfg: dict):
    branch = guild_branch(guild)
    _, sha = await gh_read(session, FILE_CONFIG, branch)
    await gh_write(session, FILE_CONFIG, cfg, sha, f"Vortex: update config", branch, guild_branch_from_id(guild_id))

async def get_warnings(session, guild: discord.Guild) -> dict:
    branch = guild_branch(guild)
    data, _ = await gh_read(session, FILE_WARNINGS, branch)
    return data or {}

async def save_warnings(session, guild: discord.Guild, warnings: dict):
    branch = guild_branch(guild)
    _, sha = await gh_read(session, FILE_WARNINGS, branch)
    await gh_write(session, FILE_WARNINGS, warnings, sha, "Vortex: update warnings", branch)

async def get_cases(session, guild: discord.Guild) -> list:
    branch = guild_branch(guild)
    data, _ = await gh_read(session, FILE_CASES, branch)
    return data or []

async def save_cases(session, guild: discord.Guild, cases: list):
    branch = guild_branch(guild)
    _, sha = await gh_read(session, FILE_CASES, branch)
    await gh_write(session, FILE_CASES, cases, sha, "Vortex: update cases", branch)

async def add_case(session, guild: discord.Guild, action: str, mod: discord.Member, target, reason: str, duration: str = None) -> int:
    cases = await get_cases(session, guild)
    case_id = len(cases) + 1
    case_data = {
        "id": case_id,
        "action": action,
        "mod_id": str(mod.id),
        "mod_name": str(mod),
        "target_id": str(target.id) if hasattr(target, "id") else str(target),
        "target_name": str(target),
        "reason": reason,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "active": True,
    }
    if duration:
        case_data["duration"] = duration
    cases.append(case_data)
    await save_cases(session, guild, cases)
    return case_id

async def get_levels(session, guild: discord.Guild) -> dict:
    branch = guild_branch(guild)
    data, _ = await gh_read(session, FILE_LEVELS, branch)
    return data or {}

async def save_levels(session, guild: discord.Guild, levels: dict):
    branch = guild_branch(guild)
    _, sha = await gh_read(session, FILE_LEVELS, branch)
    await gh_write(session, FILE_LEVELS, levels, sha, "Vortex: update levels", branch)

async def get_temp_actions(session, guild: discord.Guild) -> dict:
    branch = guild_branch(guild)
    data, _ = await gh_read(session, FILE_TEMPACTIONS, branch)
    return data or {}

async def save_temp_actions(session, guild: discord.Guild, data: dict):
    branch = guild_branch(guild)
    _, sha = await gh_read(session, FILE_TEMPACTIONS, branch)
    await gh_write(session, FILE_TEMPACTIONS, data, sha, "Vortex: update temp actions", branch)

async def get_mod_roles(session, guild: discord.Guild) -> dict:
    branch = guild_branch(guild)
    data, _ = await gh_read(session, FILE_MODROLES, branch)
    return data or {"mod_roles": [], "admin_roles": []}

async def save_mod_roles(session, guild: discord.Guild, data: dict):
    branch = guild_branch(guild)
    _, sha = await gh_read(session, FILE_MODROLES, branch)
    await gh_write(session, FILE_MODROLES, data, sha, "Vortex: update mod roles", branch)

async def get_locked_channels(session, guild: discord.Guild) -> dict:
    branch = guild_branch(guild)
    data, _ = await gh_read(session, FILE_LOCKED, branch)
    return data or {}

async def save_locked_channels(session, guild: discord.Guild, data: dict):
    branch = guild_branch(guild)
    _, sha = await gh_read(session, FILE_LOCKED, branch)
    await gh_write(session, FILE_LOCKED, data, sha, "Vortex: update locked channels", branch)

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

# ── Permission checks ─────────────────────────────────────────────────────────

def is_mod():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.guild_permissions.administrator:
            return True
        if interaction.user.guild_permissions.moderate_members:
            return True
        async with aiohttp.ClientSession() as session:
            mod_data = await get_mod_roles(session, interaction.guild)
        user_role_ids = [r.id for r in interaction.user.roles]
        if any(int(rid) in user_role_ids for rid in mod_data.get("mod_roles", [])):
            return True
        return False
    return app_commands.check(predicate)

def is_admin():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.guild_permissions.administrator:
            return True
        async with aiohttp.ClientSession() as session:
            mod_data = await get_mod_roles(session, interaction.guild)
        user_role_ids = [r.id for r in interaction.user.roles]
        if any(int(rid) in user_role_ids for rid in mod_data.get("admin_roles", [])):
            return True
        return False
    return app_commands.check(predicate)

def can_punish(mod: discord.Member, target: discord.Member) -> bool:
    """Check if mod can punish target based on role hierarchy"""
    if mod.guild.owner_id == mod.id:
        return True
    if target.id == mod.guild.owner_id:
        return False
    return mod.top_role > target.top_role

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

async def ensure_guild_files(session: aiohttp.ClientSession, guild: discord.Guild):
    """Ensure all JSON files exist on the guild's branch."""
    branch = guild_branch(guild)
    await ensure_guild_branch(session, branch)
    for filepath, default in [
        (FILE_CONFIG,      DEFAULT_CONFIG),
        (FILE_WARNINGS,    {}),
        (FILE_CASES,       []),
        (FILE_HONEYPOT,    {}),
        (FILE_TICKETS,     {}),
        (FILE_RXROLES,     {}),
        (FILE_GIVEAWAYS,   []),
        (FILE_LEVELS,      {}),
        (FILE_TEMPACTIONS, {}),
        (FILE_RAIDMODE,    {}),
        (FILE_MODROLES,    {"mod_roles": [], "admin_roles": []}),
        (FILE_LOCKED,      {}),
    ]:
        data, sha = await gh_read(session, filepath, branch)
        if sha is None:
            await gh_write(session, filepath, default, None, f"Vortex: init {filepath}", branch)

async def ensure_files():
    """Called on ready - init branches for all current guilds."""
    async with aiohttp.ClientSession() as session:
        for guild in bot.guilds:
            print(f"✅ Setting up branch for: {guild.name} ({guild.id})")
            await ensure_guild_files(session, guild)

# ══════════════════════════════════════════════════════════════════════════════
# EVENTS
# ══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_guild_join(guild: discord.Guild):
    """Auto-init branch when bot joins a new server."""
    async with aiohttp.ClientSession() as session:
        await ensure_guild_files(session, guild)
    print(f"✅ Joined and initialized: {guild.name} ({guild.id})")

@bot.event
async def on_ready():
    print(f"🌀 Vortex online as {bot.user}")
    await ensure_files()
    if not check_giveaways.is_running():
        check_giveaways.start()
    if not check_temp_actions.is_running():
        check_temp_actions.start()
    if not check_raidmode.is_running():
        check_raidmode.start()
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
        cfg = await get_config(session, member.guild)
        
        # Raid detection
        raid_mode = cfg.get("raid_mode", False)
        if raid_mode:
            # Auto-kick in raid mode
            try:
                await member.kick(reason="Raid mode active - auto-kick")
                e = discord.Embed(title="🚨 Raid Mode: Auto-kick", color=0xFF0000, timestamp=datetime.datetime.utcnow())
                e.add_field(name="User", value=f"{member} ({member.id})", inline=False)
                e.add_field(name="Account Age", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
                await send_mod_log(member.guild, cfg, e)
                return
            except:
                pass
        
        # Min account age check
        min_age = cfg.get("min_account_age", 0)
        if min_age > 0:
            age_hours = (datetime.datetime.utcnow() - member.created_at.replace(tzinfo=None)).total_seconds() / 3600
            if age_hours < min_age:
                try:
                    await member.kick(reason=f"Account too new (under {min_age}h)")
                    e = discord.Embed(title="⏰ Account Age Filter", color=0xFEE75C, timestamp=datetime.datetime.utcnow())
                    e.add_field(name="User", value=f"{member} ({member.id})", inline=False)
                    e.add_field(name="Account Age", value=f"{age_hours:.1f} hours", inline=True)
                    await send_mod_log(member.guild, cfg, e)
                    return
                except:
                    pass
    
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
        cfg = await get_config(session, member.guild)
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
    
    # Ghost ping detection
    mentions = message.mentions + message.role_mentions
    if mentions:
        ghost_ping_data = {
            "author": str(message.author),
            "author_id": str(message.author.id),
            "channel": message.channel.mention,
            "mentions": ", ".join([m.mention for m in mentions]),
            "content": message.content[:200] if message.content else "*empty*",
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
        if guild_id not in _ghost_pings:
            _ghost_pings[guild_id] = []
        _ghost_pings[guild_id].append(ghost_ping_data)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, message.guild)
    if cfg.get("logging", {}).get("message_delete") and cfg.get("mod_log"):
        e = discord.Embed(title="🗑️ Message deleted", color=0xFEE75C, timestamp=datetime.datetime.utcnow())
        e.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=True)
        e.add_field(name="Channel", value=message.channel.mention, inline=True)
        content = message.content[:1000] if message.content else "*empty*"
        if message.attachments:
            content += f"\n\n📎 Attachments: {len(message.attachments)}"
        e.add_field(name="Content", value=content, inline=False)
        await send_mod_log(message.guild, cfg, e)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not before.guild or before.author.bot or before.content == after.content:
        return
    guild_id = str(before.guild.id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, before.guild)
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
        cfg = await get_config(session, before.guild)
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
    if before.roles == after.roles and before.nick == after.nick:
        return
    guild_id = str(before.guild.id)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, before.guild)
    
    # Role change logging
    if before.roles != after.roles and cfg.get("logging", {}).get("role_change") and cfg.get("mod_log"):
        added   = [r for r in after.roles  if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        e = discord.Embed(title="🎭 Role update", color=0x9B59B6, timestamp=datetime.datetime.utcnow())
        e.add_field(name="User", value=f"{before} ({before.id})", inline=False)
        if added:
            e.add_field(name="Added", value=" ".join(r.mention for r in added), inline=True)
        if removed:
            e.add_field(name="Removed", value=" ".join(r.mention for r in removed), inline=True)
        await send_mod_log(before.guild, cfg, e)
    
    # Nickname change logging
    if before.nick != after.nick and cfg.get("logging", {}).get("nickname") and cfg.get("mod_log"):
        e = discord.Embed(title="📝 Nickname change", color=0x5865F2, timestamp=datetime.datetime.utcnow())
        e.add_field(name="User", value=f"{before} ({before.id})", inline=False)
        e.add_field(name="Before", value=before.nick or "*None*", inline=True)
        e.add_field(name="After", value=after.nick or "*None*", inline=True)
        await send_mod_log(before.guild, cfg, e)
    
    # Auto-dehoist
    if after.nick and is_hoisting(after.nick):
        new_nick = after.nick.lstrip("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ ")
        if new_nick and new_nick != after.nick:
            try:
                await after.edit(nick=new_nick, reason="Auto-dehoist")
            except:
                pass


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if not payload.guild_id:
        return
    guild_id = str(payload.guild_id)
    async with aiohttp.ClientSession() as session:
        all_rx, _ = await gh_read(session, FILE_RXROLES, guild_branch_from_id(guild_id))
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
        all_rx, _ = await gh_read(session, FILE_RXROLES, guild_branch_from_id(guild_id))
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
        cfg = await get_config(session, message.guild)

    am = cfg.get("automod", DEFAULT_CONFIG["automod"])

    # ── Honeypot check ─────────────────────────────────────────────────────────
    async with aiohttp.ClientSession() as session:
        all_hp, _ = await gh_read(session, FILE_HONEYPOT, guild_branch_from_id(guild_id))
    if all_hp:
        guild_hp = all_hp.get(guild_id, {})
        if str(message.channel.id) in guild_hp:
            # Check for protected roles (mods, admins, custom protected roles)
            protected_role_ids = cfg.get("honeypot_protected_roles", [])
            user_role_ids = [str(r.id) for r in user.roles]
            is_protected = (
                user.guild_permissions.administrator or 
                user.guild_permissions.moderate_members or
                any(rid in protected_role_ids for rid in user_role_ids)
            )
            
            if not is_protected:
                try:
                    # Delete the triggering message
                    await message.delete()
                    
                    # Ban with 1 day message deletion (deletes all messages from last 24h across ALL channels)
                    await user.ban(reason="Honeypot: Compromised/hacked account detected", delete_message_days=1)
                    
                    # Immediately unban to make it a kick (softban effect)
                    await message.guild.unban(user, reason="Honeypot kick completed")
                    
                    e = discord.Embed(title="🍯 Honeypot triggered", color=0xFF0000, timestamp=datetime.datetime.utcnow())
                    e.add_field(name="User", value=f"{user} ({user.id})", inline=True)
                    e.add_field(name="Channel", value=message.channel.mention, inline=True)
                    e.add_field(name="Action", value="Kicked + 1 day messages deleted", inline=True)
                    e.add_field(name="Reason", value="Potential compromised/hacked account", inline=False)
                    await send_mod_log(message.guild, cfg, e)
                except Exception:
                    pass
                return

    # ── XP / leveling ──────────────────────────────────────────────────────────
    async with aiohttp.ClientSession() as session:
        levels = await get_levels(session, message.guild)
        uid = str(user.id)
        entry = levels.get(uid, {"xp": 0, "level": 0, "last_msg": 0})
        now = time.time()
        if now - entry.get("last_msg", 0) > 60:
            entry["xp"] += random.randint(10, 25)
            entry["last_msg"] = now
            xp_needed = (entry["level"] + 1) * 100
            if entry["xp"] >= xp_needed:
                entry["level"] += 1
                entry["xp"]    -= xp_needed
                await message.channel.send(
                    f"🎉 {user.mention} reached **level {entry['level']}**!", delete_after=10
                )
            levels[uid] = entry
            await save_levels(session, message.guild, levels)

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
        elif action == "mute":
            until = discord.utils.utcnow() + datetime.timedelta(minutes=10)
            await user.timeout(until, reason=f"Automod: {reason}")
        elif action == "kick":
            await user.kick(reason=f"Automod: {reason}")
        elif action == "ban":
            await user.ban(reason=f"Automod: {reason}")
        e = discord.Embed(title=f"🤖 Automod: {reason}", color=0xFF6B35, timestamp=datetime.datetime.utcnow())
        e.add_field(name="User",    value=f"{user} ({user.id})", inline=True)
        e.add_field(name="Channel", value=message.channel.mention, inline=True)
        e.add_field(name="Action",  value=action, inline=True)
        await send_mod_log(message.guild, cfg, e)

    # Check if user is exempt (mod or admin)
    if user.guild_permissions.moderate_members or user.guild_permissions.administrator:
        await bot.process_commands(message)
        return

    # Invites
    if am.get("invites", {}).get("enabled") and re.search(r"discord\.gg/|discord\.com/invite/|discordapp\.com/invite/", content, re.I):
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

    # Emoji spam
    elif am.get("emojis", {}).get("enabled"):
        emoji_count = len(re.findall(r"<a?:\w+:\d+>|[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]", content))
        if emoji_count > am["emojis"].get("max", 10):
            await automod_action("delete", "Excessive emojis")

    # Newline spam
    elif am.get("newlines", {}).get("enabled"):
        newline_count = content.count('\n')
        if newline_count > am["newlines"].get("max", 10):
            await automod_action("delete", "Excessive newlines")

    # Zalgo detection
    elif am.get("zalgo", {}).get("enabled") and contains_zalgo(content):
        await automod_action("delete", "Zalgo text")

    await bot.process_commands(message)


# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS - BAN
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="ban", description="Ban a member")
@app_commands.describe(member="Member to ban", reason="Reason", delete_days="Days of messages to delete (0-7)")
@is_mod()
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason", delete_days: int = 0):
    await interaction.response.defer(ephemeral=True)
    
    if not can_punish(interaction.user, member):
        await interaction.followup.send("❌ You cannot ban this user (role hierarchy).", ephemeral=True)
        return
    
    delete_days = min(max(delete_days, 0), 7)
    
    try:
        await member.send(f"❌ You have been **banned** from **{interaction.guild.name}**.\nReason: {reason}")
    except Exception:
        pass
    
    await member.ban(reason=reason, delete_message_days=delete_days)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "ban", interaction.user, member, reason)
    
    e = mod_embed(0xED4245, "Member banned", [
        ("User", f"{member} ({member.id})", True),
        ("Mod",  f"{interaction.user}", True),
        ("Reason", reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"✅ Banned **{member}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="hackban", description="Ban a user by ID (even if not in server)")
@app_commands.describe(user_id="User ID to ban", reason="Reason", delete_days="Days of messages to delete (0-7)")
@is_mod()
async def hackban(interaction: discord.Interaction, user_id: str, reason: str = "No reason", delete_days: int = 0):
    await interaction.response.defer(ephemeral=True)
    
    try:
        user_id = int(user_id)
    except ValueError:
        await interaction.followup.send("❌ Invalid user ID.", ephemeral=True)
        return
    
    # Check if user is in server
    member = interaction.guild.get_member(user_id)
    if member:
        if not can_punish(interaction.user, member):
            await interaction.followup.send("❌ You cannot ban this user (role hierarchy).", ephemeral=True)
            return
    
    delete_days = min(max(delete_days, 0), 7)
    
    try:
        user = await bot.fetch_user(user_id)
        await interaction.guild.ban(user, reason=reason, delete_message_days=delete_days)
        
        async with aiohttp.ClientSession() as session:
            cfg = await get_config(session, interaction.guild)
            case_id = await add_case(session, interaction.guild, "hackban", interaction.user, user, reason)
        
        e = mod_embed(0xED4245, "User hackbanned", [
            ("User", f"{user} ({user.id})", True),
            ("Mod",  f"{interaction.user}", True),
            ("Reason", reason, False),
        ], case_id)
        await send_mod_log(interaction.guild, cfg, e)
        await interaction.followup.send(f"✅ Hackbanned **{user}** | Case #{case_id}", ephemeral=True)
    except Exception as ex:
        await interaction.followup.send(f"❌ Failed: {ex}", ephemeral=True)




@bot.tree.command(name="softban", description="Ban and immediately unban to delete messages")
@app_commands.describe(member="Member to softban", reason="Reason")
@is_mod()
async def softban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if not can_punish(interaction.user, member):
        await interaction.followup.send("❌ You cannot softban this user (role hierarchy).", ephemeral=True)
        return
    
    try:
        await member.send(f"🔨 You have been **softbanned** from **{interaction.guild.name}**.\nReason: {reason}\nYou may rejoin with an invite.")
    except Exception:
        pass
    
    await member.ban(reason=reason, delete_message_days=1)
    await member.unban(reason="Softban - auto unban")
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "softban", interaction.user, member, reason)
    
    e = mod_embed(0xFEE75C, "Member softbanned", [
        ("User", f"{member} ({member.id})", True),
        ("Mod",  f"{interaction.user}", True),
        ("Reason", reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"✅ Softbanned **{member}** (messages deleted) | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="tempban", description="Temporarily ban a member")
@app_commands.describe(member="Member to ban", duration="Duration (e.g., 1h, 1d, 1w)", reason="Reason")
@is_mod()
async def tempban(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if not can_punish(interaction.user, member):
        await interaction.followup.send("❌ You cannot ban this user (role hierarchy).", ephemeral=True)
        return
    
    td = parse_duration(duration)
    if td is None:
        await interaction.followup.send("❌ Invalid duration. Use format like 1h, 30m, 1d, 1w", ephemeral=True)
        return
    
    end_time = discord.utils.utcnow() + td
    
    try:
        await member.send(f"❌ You have been **temporarily banned** from **{interaction.guild.name}**.\nDuration: {format_timedelta(td)}\nReason: {reason}")
    except Exception:
        pass
    
    await member.ban(reason=f"Tempban: {reason}")
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "tempban", interaction.user, member, reason, duration)
        
        # Store temp action
        temp_data = await get_temp_actions(session)
        guild_id = str(interaction.guild_id)
        if guild_id not in temp_data:
            temp_data[guild_id] = []
        temp_data[guild_id].append({
            "type": "unban",
            "user_id": str(member.id),
            "guild_id": guild_id,
            "end_time": end_time.isoformat(),
            "case_id": case_id
        })
        await save_temp_actions(session, temp_data)
    
    e = mod_embed(0xED4245, "Member temporarily banned", [
        ("User", f"{member} ({member.id})", True),
        ("Mod",  f"{interaction.user}", True),
        ("Duration", format_timedelta(td), True),
        ("Reason", reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"✅ Tempbanned **{member}** for {format_timedelta(td)} | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="massban", description="Ban multiple users at once")
@app_commands.describe(users="User IDs or mentions (space-separated)", reason="Reason")
@is_admin()
async def massban(interaction: discord.Interaction, users: str, reason: str = "Mass ban"):
    await interaction.response.defer(ephemeral=True)
    
    # Parse user IDs
    user_ids = re.findall(r'\d{17,20}', users)
    if not user_ids:
        await interaction.followup.send("❌ No valid user IDs found.", ephemeral=True)
        return
    
    banned = 0
    failed = 0
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        
        for uid in user_ids[:20]:  # Limit to 20 at once
            try:
                uid_int = int(uid)
                member = interaction.guild.get_member(uid_int)
                
                if member and not can_punish(interaction.user, member):
                    failed += 1
                    continue
                
                user = await bot.fetch_user(uid_int)
                await interaction.guild.ban(user, reason=reason)
                banned += 1
            except:
                failed += 1
        
        if banned > 0:
            case_id = await add_case(session, interaction.guild, "massban", interaction.user, 
                                    type('Obj', (object,), {'id': 0, '__str__': lambda s: f"{banned} users"})(), 
                                    reason)
    
    await interaction.followup.send(f"✅ Banned **{banned}** users. Failed: {failed}", ephemeral=True)


@bot.tree.command(name="unban", description="Unban a user by ID")
@app_commands.describe(user_id="User ID to unban", reason="Reason")
@is_mod()
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        async with aiohttp.ClientSession() as session:
            cfg  = await get_config(session, interaction.guild)
            case_id = await add_case(session, interaction.guild, "unban", interaction.user, user, reason)
        e = mod_embed(0x57F287, "Member unbanned", [
            ("User", f"{user} ({user.id})", True),
            ("Mod",  f"{interaction.user}", True),
            ("Reason", reason, False),
        ], case_id)
        await send_mod_log(interaction.guild, cfg, e)
        await interaction.followup.send(f"✅ Unbanned **{user}** | Case #{case_id}", ephemeral=True)
    except Exception as ex:
        await interaction.followup.send(f"❌ Failed: {ex}", ephemeral=True)


@bot.tree.command(name="banlist", description="List all banned users")
@is_mod()
async def banlist(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    bans = [entry async for entry in interaction.guild.bans(limit=50)]
    
    e = discord.Embed(title=f"🔨 Ban List ({len(bans)} users)", color=0xED4245)
    
    if not bans:
        e.description = "No banned users."
    else:
        for i, entry in enumerate(bans[:25]):
            reason = entry.reason or "No reason"
            e.add_field(name=f"{entry.user}", value=f"ID: {entry.user.id}\n{reason[:50]}", inline=True)
    
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="checkban", description="Check if a user is banned")
@app_commands.describe(user_id="User ID to check")
@is_mod()
async def checkban(interaction: discord.Interaction, user_id: str):
    await interaction.response.defer(ephemeral=True)
    
    try:
        user_id = int(user_id)
        user = await bot.fetch_user(user_id)
        
        try:
            ban_entry = await interaction.guild.fetch_ban(user)
            e = discord.Embed(title="🚫 User is banned", color=0xED4245)
            e.add_field(name="User", value=f"{user} ({user.id})", inline=False)
            e.add_field(name="Reason", value=ban_entry.reason or "No reason", inline=False)
        except discord.NotFound:
            e = discord.Embed(title="✅ User is not banned", color=0x57F287)
            e.add_field(name="User", value=f"{user} ({user.id})", inline=False)
        
        await interaction.followup.send(embed=e, ephemeral=True)
    except Exception as ex:
        await interaction.followup.send(f"❌ Failed: {ex}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS - KICK
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="kick", description="Kick a member")
@app_commands.describe(member="Member to kick", reason="Reason")
@is_mod()
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if not can_punish(interaction.user, member):
        await interaction.followup.send("❌ You cannot kick this user (role hierarchy).", ephemeral=True)
        return
    
    try:
        await member.send(f"👢 You have been **kicked** from **{interaction.guild.name}**.\nReason: {reason}")
    except Exception:
        pass
    
    await member.kick(reason=reason)
    
    async with aiohttp.ClientSession() as session:
        cfg     = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "kick", interaction.user, member, reason)
    
    e = mod_embed(0xFEE75C, "Member kicked", [
        ("User", f"{member} ({member.id})", True),
        ("Mod",  f"{interaction.user}", True),
        ("Reason", reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"✅ Kicked **{member}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="masskick", description="Kick multiple users at once")
@app_commands.describe(users="User mentions or IDs (space-separated)", reason="Reason")
@is_admin()
async def masskick(interaction: discord.Interaction, users: str, reason: str = "Mass kick"):
    await interaction.response.defer(ephemeral=True)
    
    user_ids = re.findall(r'\d{17,20}', users)
    if not user_ids:
        await interaction.followup.send("❌ No valid user IDs found.", ephemeral=True)
        return
    
    kicked = 0
    failed = 0
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        
        for uid in user_ids[:20]:
            try:
                member = interaction.guild.get_member(int(uid))
                if member and can_punish(interaction.user, member):
                    await member.kick(reason=reason)
                    kicked += 1
                else:
                    failed += 1
            except:
                failed += 1
        
        if kicked > 0:
            case_id = await add_case(session, interaction.guild, "masskick", interaction.user,
                                    type('Obj', (object,), {'id': 0, '__str__': lambda s: f"{kicked} users"})(),
                                    reason)
    
    await interaction.followup.send(f"✅ Kicked **{kicked}** users. Failed: {failed}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS - MUTE
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="mute", description="Timeout a member")
@app_commands.describe(member="Member to mute", duration="Duration (e.g., 10m, 1h, 1d)", reason="Reason")
@is_mod()
async def mute(interaction: discord.Interaction, member: discord.Member, duration: str = "10m", reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if not can_punish(interaction.user, member):
        await interaction.followup.send("❌ You cannot mute this user (role hierarchy).", ephemeral=True)
        return
    
    td = parse_duration(duration)
    if td is None:
        # Default to 10 minutes
        td = datetime.timedelta(minutes=10)
    
    # Discord max timeout is 28 days
    max_timeout = datetime.timedelta(days=28)
    if td > max_timeout:
        td = max_timeout
    
    until = discord.utils.utcnow() + td
    await member.timeout(until, reason=reason)
    
    async with aiohttp.ClientSession() as session:
        cfg     = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "mute", interaction.user, member, reason, duration)
    
    e = mod_embed(0x9B59B6, "Member muted", [
        ("User",     f"{member} ({member.id})", True),
        ("Mod",      f"{interaction.user}", True),
        ("Duration", format_timedelta(td), True),
        ("Reason",   reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"✅ Muted **{member}** for {format_timedelta(td)} | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="unmute", description="Remove timeout from a member")
@app_commands.describe(member="Member to unmute", reason="Reason")
@is_mod()
async def unmute(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    await member.timeout(None, reason=reason)
    async with aiohttp.ClientSession() as session:
        cfg     = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "unmute", interaction.user, member, reason)
    e = mod_embed(0x57F287, "Member unmuted", [
        ("User", f"{member} ({member.id})", True),
        ("Mod",  f"{interaction.user}", True),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"✅ Unmuted **{member}** | Case #{case_id}", ephemeral=True)




# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS - WARNINGS
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="warn", description="Warn a member")
@app_commands.describe(member="Member to warn", points="Warning points (default 1)", reason="Reason")
@is_mod()
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason", points: int = 1):
    await interaction.response.defer(ephemeral=True)
    
    if not can_punish(interaction.user, member):
        await interaction.followup.send("❌ You cannot warn this user (role hierarchy).", ephemeral=True)
        return
    
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        cfg      = await get_config(session, interaction.guild)
        warnings = await get_warnings(session, interaction.guild)
        uid      = str(member.id)
        if uid not in warnings:
            warnings[uid] = []
        
        warn_data = {
            "reason": reason,
            "mod": str(interaction.user),
            "mod_id": str(interaction.user.id),
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "points": points,
        }
        warnings[uid].append(warn_data)
        await save_warnings(session, interaction.guild, warnings)
        case_id = await add_case(session, guild, "warn", interaction.user, member, reason)
    
    total_points = sum(w.get("points", 1) for w in warnings[uid])
    count = len(warnings[uid])
    
    try:
        await member.send(f"⚠️ You have been warned in **{interaction.guild.name}**.\nReason: {reason}\nTotal warnings: {count} ({total_points} points)")
    except Exception:
        pass
    
    e = mod_embed(0xFEE75C, "Member warned", [
        ("User",     f"{member} ({member.id})", True),
        ("Mod",      f"{interaction.user}", True),
        ("Warnings", str(count), True),
        ("Points",   str(total_points), True),
        ("Reason",   reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"⚠️ Warned **{member}** (Warning #{count}, {total_points} points) | Case #{case_id}", ephemeral=True)

    # Auto-punish at thresholds
    punishments = cfg.get("warning_punishments", DEFAULT_CONFIG["warning_punishments"])
    for threshold, action in sorted(punishments.items(), key=lambda x: int(x[0])):
        if total_points >= int(threshold):
            if action == "mute":
                until = discord.utils.utcnow() + datetime.timedelta(hours=1)
                await member.timeout(until, reason=f"{threshold} warning points accumulated")
                await interaction.channel.send(f"🔇 **{member}** auto-muted (1h) for {threshold} warning points.", delete_after=10)
            elif action == "kick":
                await member.kick(reason=f"{threshold} warning points accumulated")
                await interaction.channel.send(f"👢 **{member}** auto-kicked for {threshold} warning points.", delete_after=10)
                break
            elif action == "ban":
                await member.ban(reason=f"{threshold} warning points accumulated")
                await interaction.channel.send(f"🔨 **{member}** auto-banned for {threshold} warning points.", delete_after=10)
                break


@bot.tree.command(name="warnings", description="View warnings for a member")
@app_commands.describe(member="Member to check")
@is_mod()
async def warnings(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        warns = await get_warnings(session, interaction.guild)
    user_warns = warns.get(str(member.id), [])
    
    total_points = sum(w.get("points", 1) for w in user_warns)
    e = discord.Embed(title=f"⚠️ Warnings for {member}", color=0xFEE75C)
    e.description = f"**Total:** {len(user_warns)} warnings ({total_points} points)"
    
    if not user_warns:
        e.description = "No warnings."
    else:
        for i, w in enumerate(user_warns[-10:], 1):
            points = w.get("points", 1)
            timestamp = int(datetime.datetime.fromisoformat(w['timestamp']).timestamp())
            e.add_field(name=f"#{i} — {w['mod']} (+{points})", value=f"{w['reason']}\n<t:{timestamp}:R>", inline=False)
    
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="delwarn", description="Delete a specific warning")
@app_commands.describe(member="Member", warn_number="Warning number to delete")
@is_mod()
async def delwarn(interaction: discord.Interaction, member: discord.Member, warn_number: int):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        warns = await get_warnings(session, interaction.guild)
        user_warns = warns.get(str(member.id), [])
        
        if warn_number < 1 or warn_number > len(user_warns):
            await interaction.followup.send(f"❌ Warning #{warn_number} not found. User has {len(user_warns)} warnings.", ephemeral=True)
            return
        
        removed = user_warns.pop(warn_number - 1)
        warns[str(member.id)] = user_warns
        await save_warnings(session, interaction.guild, warns)
    
    await interaction.followup.send(f"✅ Removed warning #{warn_number} from **{member}**\nReason was: {removed['reason']}", ephemeral=True)


@bot.tree.command(name="clearwarnings", description="Clear all warnings for a member")
@app_commands.describe(member="Member to clear warnings for")
@is_admin()
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        warns = await get_warnings(session, interaction.guild)
        warns[str(member.id)] = []
        await save_warnings(session, interaction.guild, warns)
    await interaction.followup.send(f"✅ Cleared all warnings for **{member}**", ephemeral=True)


@bot.tree.command(name="editwarn", description="Edit a warning's reason")
@app_commands.describe(member="Member", warn_number="Warning number", new_reason="New reason")
@is_mod()
async def editwarn(interaction: discord.Interaction, member: discord.Member, warn_number: int, new_reason: str):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        warns = await get_warnings(session, interaction.guild)
        user_warns = warns.get(str(member.id), [])
        
        if warn_number < 1 or warn_number > len(user_warns):
            await interaction.followup.send(f"❌ Warning #{warn_number} not found.", ephemeral=True)
            return
        
        user_warns[warn_number - 1]["reason"] = new_reason
        warns[str(member.id)] = user_warns
        await save_warnings(session, interaction.guild, warns)
    
    await interaction.followup.send(f"✅ Updated warning #{warn_number} for **{member}**\nNew reason: {new_reason}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS - SLOWMODE & LOCKDOWN
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="slowmode", description="Set channel slowmode")
@app_commands.describe(duration="Slowmode duration (e.g., 5s, 1m, 0 to disable)", channel="Channel (default: current)")
@is_mod()
async def slowmode(interaction: discord.Interaction, duration: str, channel: discord.TextChannel = None):
    await interaction.response.defer(ephemeral=True)
    channel = channel or interaction.channel
    
    if duration.lower() in ('0', 'off', 'disable'):
        await channel.edit(slowmode_delay=0)
        await interaction.followup.send(f"✅ Slowmode disabled in {channel.mention}", ephemeral=True)
        return
    
    td = parse_duration(duration)
    if td is None:
        await interaction.followup.send("❌ Invalid duration. Use format like 5s, 30s, 1m", ephemeral=True)
        return
    
    seconds = int(td.total_seconds())
    if seconds > 21600:  # Discord max is 6 hours
        seconds = 21600
    
    await channel.edit(slowmode_delay=seconds)
    await interaction.followup.send(f"✅ Slowmode set to **{format_timedelta(td)}** in {channel.mention}", ephemeral=True)


@bot.tree.command(name="lock", description="Lock a channel")
@app_commands.describe(channel="Channel to lock (default: current)", reason="Reason")
@is_mod()
async def lock(interaction: discord.Interaction, channel: discord.abc.GuildChannel = None, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    channel = channel or interaction.channel
    guild_id = str(interaction.guild_id)
    
    if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
        await interaction.followup.send("❌ Invalid channel type.", ephemeral=True)
        return
    
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    overwrite.connect = False
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, guild, "lock", interaction.user, 
                                type('Obj', (object,), {'id': channel.id, '__str__': lambda s: f"#{channel.name}"})(), reason)
        
        locked = await get_locked_channels(session, interaction.guild)
        locked[str(channel.id)] = {"channel_name": channel.name, "reason": reason, "timestamp": datetime.datetime.utcnow().isoformat()}
        await save_locked_channels(session, interaction.guild, locked)
    
    e = mod_embed(0xED4245, "Channel locked", [
        ("Channel", channel.mention, True),
        ("Mod", str(interaction.user), True),
        ("Reason", reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"🔒 Locked {channel.mention} | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="unlock", description="Unlock a channel")
@app_commands.describe(channel="Channel to unlock (default: current)", reason="Reason")
@is_mod()
async def unlock(interaction: discord.Interaction, channel: discord.abc.GuildChannel = None, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    channel = channel or interaction.channel
    guild_id = str(interaction.guild_id)
    
    if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
        await interaction.followup.send("❌ Invalid channel type.", ephemeral=True)
        return
    
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    overwrite.connect = None
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, guild, "unlock", interaction.user,
                                type('Obj', (object,), {'id': channel.id, '__str__': lambda s: f"#{channel.name}"})(), reason)
        
        locked = await get_locked_channels(session, interaction.guild)
        locked.pop(str(channel.id), None)
        await save_locked_channels(session, interaction.guild, locked)
    
    e = mod_embed(0x57F287, "Channel unlocked", [
        ("Channel", channel.mention, True),
        ("Mod", str(interaction.user), True),
        ("Reason", reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"🔓 Unlocked {channel.mention} | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="lockall", description="Lock all text channels")
@app_commands.describe(reason="Reason")
@is_admin()
async def lockall(interaction: discord.Interaction, reason: str = "Server lockdown"):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    locked_count = 0
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        locked = await get_locked_channels(session, interaction.guild)
        
        for channel in interaction.guild.text_channels:
            try:
                overwrite = channel.overwrites_for(interaction.guild.default_role)
                overwrite.send_messages = False
                await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
                locked[str(channel.id)] = {"channel_name": channel.name, "reason": reason, "timestamp": datetime.datetime.utcnow().isoformat()}
                locked_count += 1
            except:
                pass
        
        await save_locked_channels(session, interaction.guild, locked)
        case_id = await add_case(session, guild, "lockall", interaction.user,
                                type('Obj', (object,), {'id': 0, '__str__': lambda s: f"{locked_count} channels"})(), reason)
    
    await interaction.followup.send(f"🔒 Locked **{locked_count}** channels | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="unlockall", description="Unlock all locked channels")
@app_commands.describe(reason="Reason")
@is_admin()
async def unlockall(interaction: discord.Interaction, reason: str = "Unlocking server"):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    unlocked_count = 0
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        locked = await get_locked_channels(session, interaction.guild)
        
        for channel_id in list(locked.keys()):
            channel = interaction.guild.get_channel(int(channel_id))
            if channel:
                try:
                    overwrite = channel.overwrites_for(interaction.guild.default_role)
                    overwrite.send_messages = None
                    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
                    unlocked_count += 1
                except:
                    pass
        
        await save_locked_channels(session, interaction.guild, {})
        case_id = await add_case(session, guild, "unlockall", interaction.user,
                                type('Obj', (object,), {'id': 0, '__str__': lambda s: f"{unlocked_count} channels"})(), reason)
    
    await interaction.followup.send(f"🔓 Unlocked **{unlocked_count}** channels | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="templock", description="Temporarily lock a channel")
@app_commands.describe(duration="Duration (e.g., 30m, 1h)", channel="Channel (default: current)", reason="Reason")
@is_mod()
async def templock(interaction: discord.Interaction, duration: str, channel: discord.abc.GuildChannel = None, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    channel = channel or interaction.channel
    
    td = parse_duration(duration)
    if td is None:
        await interaction.followup.send("❌ Invalid duration.", ephemeral=True)
        return
    
    # Lock the channel
    await lock(interaction, channel, f"Templock: {reason}")
    
    # Schedule unlock
    end_time = discord.utils.utcnow() + td
    async with aiohttp.ClientSession() as session:
        temp_data = await get_temp_actions(session)
        guild_id = str(interaction.guild_id)
        if guild_id not in temp_data:
            temp_data[guild_id] = []
        temp_data[guild_id].append({
            "type": "unlock",
            "channel_id": str(channel.id),
            "guild_id": guild_id,
            "end_time": end_time.isoformat(),
        })
        await save_temp_actions(session, temp_data)
    
    await interaction.followup.send(f"🔒 Locked {channel.mention} for {format_timedelta(td)}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS - PURGE
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="purge", description="Delete messages in bulk")
@app_commands.describe(amount="Number of messages to delete (max 100)", user="Only delete from this user (optional)")
@is_mod()
async def purge(interaction: discord.Interaction, amount: int, user: discord.Member = None):
    await interaction.response.defer(ephemeral=True)
    amount = min(amount, 100)
    
    def check(msg):
        if user:
            return msg.author.id == user.id
        return True
    
    deleted = await interaction.channel.purge(limit=amount, check=check)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "purge", interaction.user,
                                type('Obj', (object,), {'id': 0, '__str__': lambda s: f"{len(deleted)} messages"})(),
                                f"In #{interaction.channel.name}")
    
    user_text = f" from **{user}**" if user else ""
    await interaction.followup.send(f"✅ Deleted **{len(deleted)}** messages{user_text} | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="purgecontains", description="Delete messages containing specific text")
@app_commands.describe(text="Text to search for", amount="Number of messages to search (max 100)")
@is_mod()
async def purgecontains(interaction: discord.Interaction, text: str, amount: int = 50):
    await interaction.response.defer(ephemeral=True)
    amount = min(amount, 100)
    
    def check(msg):
        return text.lower() in msg.content.lower()
    
    deleted = await interaction.channel.purge(limit=amount, check=check)
    await interaction.followup.send(f"✅ Deleted **{len(deleted)}** messages containing '{text}'", ephemeral=True)


@bot.tree.command(name="purgebots", description="Delete bot messages")
@app_commands.describe(amount="Number of messages to search (max 100)")
@is_mod()
async def purgebots(interaction: discord.Interaction, amount: int = 50):
    await interaction.response.defer(ephemeral=True)
    amount = min(amount, 100)
    
    def check(msg):
        return msg.author.bot
    
    deleted = await interaction.channel.purge(limit=amount, check=check)
    await interaction.followup.send(f"✅ Deleted **{len(deleted)}** bot messages", ephemeral=True)


@bot.tree.command(name="purgeattachments", description="Delete messages with attachments")
@app_commands.describe(amount="Number of messages to search (max 100)")
@is_mod()
async def purgeattachments(interaction: discord.Interaction, amount: int = 50):
    await interaction.response.defer(ephemeral=True)
    amount = min(amount, 100)
    
    def check(msg):
        return bool(msg.attachments)
    
    deleted = await interaction.channel.purge(limit=amount, check=check)
    await interaction.followup.send(f"✅ Deleted **{len(deleted)}** messages with attachments", ephemeral=True)


@bot.tree.command(name="purgelinks", description="Delete messages containing links")
@app_commands.describe(amount="Number of messages to search (max 100)")
@is_mod()
async def purgelinks(interaction: discord.Interaction, amount: int = 50):
    await interaction.response.defer(ephemeral=True)
    amount = min(amount, 100)
    
    def check(msg):
        return bool(re.search(r'https?://', msg.content))
    
    deleted = await interaction.channel.purge(limit=amount, check=check)
    await interaction.followup.send(f"✅ Deleted **{len(deleted)}** messages with links", ephemeral=True)


@bot.tree.command(name="nuke", description="Clone and delete a channel (wipe all messages)")
@app_commands.describe(channel="Channel to nuke (default: current)", reason="Reason")
@is_admin()
async def nuke(interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "Channel nuked"):
    await interaction.response.defer(ephemeral=True)
    channel = channel or interaction.channel
    
    # Clone the channel
    new_channel = await channel.clone(reason=reason)
    await new_channel.edit(position=channel.position)
    
    # Delete the old channel
    await channel.delete(reason=reason)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "nuke", interaction.user,
                                type('Obj', (object,), {'id': new_channel.id, '__str__': lambda s: f"#{new_channel.name}"})(), reason)
    
    await new_channel.send(f"💥 Channel nuked by {interaction.user.mention} | Case #{case_id}")


# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS - VOICE
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="vckick", description="Kick a user from voice channel")
@app_commands.describe(member="Member to kick", reason="Reason")
@is_mod()
async def vckick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if not member.voice:
        await interaction.followup.send("❌ User is not in a voice channel.", ephemeral=True)
        return
    
    await member.move_to(None, reason=reason)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "vckick", interaction.user, member, reason)
    
    await interaction.followup.send(f"✅ Kicked **{member}** from voice | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="vcmove", description="Move a user to another voice channel")
@app_commands.describe(member="Member to move", channel="Target voice channel", reason="Reason")
@is_mod()
async def vcmove(interaction: discord.Interaction, member: discord.Member, channel: discord.VoiceChannel, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if not member.voice:
        await interaction.followup.send("❌ User is not in a voice channel.", ephemeral=True)
        return
    
    await member.move_to(channel, reason=reason)
    await interaction.followup.send(f"✅ Moved **{member}** to {channel.mention}", ephemeral=True)


@bot.tree.command(name="vcmute", description="Server mute a user in voice")
@app_commands.describe(member="Member to mute", reason="Reason")
@is_mod()
async def vcmute(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if not member.voice:
        await interaction.followup.send("❌ User is not in a voice channel.", ephemeral=True)
        return
    
    await member.edit(mute=True, reason=reason)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "vcmute", interaction.user, member, reason)
    
    await interaction.followup.send(f"✅ Server muted **{member}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="vcunmute", description="Remove server mute from user")
@app_commands.describe(member="Member to unmute", reason="Reason")
@is_mod()
async def vcunmute(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    await member.edit(mute=False, reason=reason)
    await interaction.followup.send(f"✅ Unmuted **{member}**", ephemeral=True)


@bot.tree.command(name="vcdeafen", description="Server deafen a user")
@app_commands.describe(member="Member to deafen", reason="Reason")
@is_mod()
async def vcdeafen(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if not member.voice:
        await interaction.followup.send("❌ User is not in a voice channel.", ephemeral=True)
        return
    
    await member.edit(deafen=True, reason=reason)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "vcdeafen", interaction.user, member, reason)
    
    await interaction.followup.send(f"✅ Server deafened **{member}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="vcundeafen", description="Remove server deafen from user")
@app_commands.describe(member="Member to undeafen", reason="Reason")
@is_mod()
async def vcundeafen(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    await member.edit(deafen=False, reason=reason)
    await interaction.followup.send(f"✅ Undeafened **{member}**", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS - ROLE & NICKNAME
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="roleadd", description="Add a role to a member")
@app_commands.describe(member="Member", role="Role to add", reason="Reason")
@is_mod()
async def roleadd(interaction: discord.Interaction, member: discord.Member, role: discord.Role, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await interaction.followup.send("❌ You cannot add a role equal to or higher than your highest role.", ephemeral=True)
        return
    
    await member.add_roles(role, reason=reason)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "roleadd", interaction.user, member, f"+{role.name}: {reason}")
    
    await interaction.followup.send(f"✅ Added **{role.name}** to **{member}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="roleremove", description="Remove a role from a member")
@app_commands.describe(member="Member", role="Role to remove", reason="Reason")
@is_mod()
async def roleremove(interaction: discord.Interaction, member: discord.Member, role: discord.Role, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if role not in member.roles:
        await interaction.followup.send("❌ User doesn't have this role.", ephemeral=True)
        return
    
    await member.remove_roles(role, reason=reason)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, interaction.guild, "roleremove", interaction.user, member, f"-{role.name}: {reason}")
    
    await interaction.followup.send(f"✅ Removed **{role.name}** from **{member}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="nick", description="Change a member's nickname")
@app_commands.describe(member="Member", nickname="New nickname (leave empty to reset)", reason="Reason")
@is_mod()
async def nick(interaction: discord.Interaction, member: discord.Member, nickname: str = None, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    
    if not can_punish(interaction.user, member):
        await interaction.followup.send("❌ You cannot change this user's nickname (role hierarchy).", ephemeral=True)
        return
    
    await member.edit(nick=nickname, reason=reason)
    
    action = "changed" if nickname else "reset"
    await interaction.followup.send(f"✅ Nickname {action} for **{member}**" + (f" to **{nickname}**" if nickname else ""), ephemeral=True)


@bot.tree.command(name="dehoist", description="Remove hoisting characters from a member's nickname")
@app_commands.describe(member="Member to dehoist")
@is_mod()
async def dehoist(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    
    if not member.nick and not is_hoisting(member.name):
        await interaction.followup.send("❌ User's name doesn't have hoisting characters.", ephemeral=True)
        return
    
    current = member.nick or member.name
    new_nick = current.lstrip("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ ")
    
    if new_nick == current:
        await interaction.followup.send("❌ No hoisting characters found.", ephemeral=True)
        return
    
    await member.edit(nick=new_nick if member.nick else new_nick, reason="Auto-dehoist")
    await interaction.followup.send(f"✅ Dehoisted **{member}** → **{new_nick}**", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS - QUARANTINE
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="quarantine", description="Quarantine a member (restrict to quarantine role)")
@app_commands.describe(member="Member to quarantine", reason="Reason")
@is_mod()
async def quarantine(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
    
    quarantine_role_id = cfg.get("quarantine_role")
    if not quarantine_role_id:
        await interaction.followup.send("❌ Quarantine role not set. Use `/setup_quarantine` first.", ephemeral=True)
        return
    
    quarantine_role = interaction.guild.get_role(int(quarantine_role_id))
    if not quarantine_role:
        await interaction.followup.send("❌ Quarantine role not found.", ephemeral=True)
        return
    
    # Store old roles
    old_roles = [r.id for r in member.roles if r.name != "@everyone"]
    
    # Remove all roles and add quarantine
    try:
        await member.edit(roles=[quarantine_role], reason=f"Quarantine: {reason}")
    except:
        await interaction.followup.send("❌ Failed to quarantine user.", ephemeral=True)
        return
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        case_id = await add_case(session, guild, "quarantine", interaction.user, member, reason)
        
        # Store old roles for later restoration
        all_locked, _ = await gh_read(session, FILE_LOCKED, guild_branch_from_id(guild_id))
        if not all_locked:
            all_locked = {}
        if "quarantine_roles" not in all_locked:
            all_locked["quarantine_roles"] = {}
        all_locked["quarantine_roles"][str(member.id)] = old_roles
        await gh_write(session, FILE_LOCKED, all_locked, None, "Store quarantine roles", guild_branch_from_id(guild_id))
    
    e = mod_embed(0x9B59B6, "Member quarantined", [
        ("User", f"{member} ({member.id})", True),
        ("Mod", str(interaction.user), True),
        ("Reason", reason, False),
    ], case_id)
    await send_mod_log(interaction.guild, cfg, e)
    await interaction.followup.send(f"🔒 Quarantined **{member}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="unquarantine", description="Release a member from quarantine")
@app_commands.describe(member="Member to release", reason="Reason")
@is_mod()
async def unquarantine(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        
        # Get stored roles
        all_locked, _ = await gh_read(session, FILE_LOCKED, guild_branch_from_id(guild_id))
        stored_roles = []
        if all_locked and "quarantine_roles" in all_locked:
            stored_roles = all_locked["quarantine_roles"].pop(str(member.id), [])
            await gh_write(session, FILE_LOCKED, all_locked, None, "Restore quarantine roles", guild_branch_from_id(guild_id))
    
    # Remove quarantine role
    quarantine_role_id = cfg.get("quarantine_role")
    if quarantine_role_id:
        quarantine_role = interaction.guild.get_role(int(quarantine_role_id))
        if quarantine_role and quarantine_role in member.roles:
            await member.remove_roles(quarantine_role, reason=f"Unquarantine: {reason}")
    
    # Restore old roles
    if stored_roles:
        roles_to_add = [interaction.guild.get_role(int(rid)) for rid in stored_roles]
        roles_to_add = [r for r in roles_to_add if r]
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason=f"Unquarantine: restore roles")
    
    async with aiohttp.ClientSession() as session:
        case_id = await add_case(session, guild, "unquarantine", interaction.user, member, reason)
    
    await interaction.followup.send(f"✅ Released **{member}** from quarantine | Case #{case_id}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS - RAID PROTECTION
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="raidmode", description="Toggle raid mode")
@app_commands.describe(enabled="Enable or disable raid mode")
@is_admin()
async def raidmode_cmd(interaction: discord.Interaction, enabled: bool):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg["raid_mode"] = enabled
        await save_config(session, interaction.guild, cfg)
        
        case_id = await add_case(session, guild, "raidmode", interaction.user,
                                type('Obj', (object,), {'id': 0, '__str__': lambda s: "Server"})(),
                                f"Raid mode: {'enabled' if enabled else 'disabled'}")
    
    status = "enabled 🚨" if enabled else "disabled ✅"
    await interaction.followup.send(f"🛡️ Raid mode **{status}** | Case #{case_id}", ephemeral=True)


@bot.tree.command(name="panic", description="Emergency lockdown - lock all channels and enable raid mode")
@is_admin()
async def panic(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg["raid_mode"] = True
        await save_config(session, interaction.guild, cfg)
    
    # Lock all channels
    locked_count = 0
    for channel in interaction.guild.text_channels:
        try:
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason="PANIC MODE")
            locked_count += 1
        except:
            pass
    
    # Announce
    for channel in interaction.guild.text_channels[:3]:
        try:
            await channel.send("🚨 **PANIC MODE ACTIVATED** - Server is on lockdown. Please wait for staff to resolve the issue.")
            break
        except:
            pass
    
    await interaction.followup.send(f"🚨 **PANIC MODE** - Locked {locked_count} channels, raid mode enabled.", ephemeral=True)


@bot.tree.command(name="unpanic", description="Disable panic mode")
@is_admin()
async def unpanic(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg["raid_mode"] = False
        await save_config(session, interaction.guild, cfg)
        
        await save_locked_channels(session, interaction.guild, {})
    
    await interaction.followup.send("✅ Panic mode disabled. Use `/unlockall` to unlock channels.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# INFORMATION COMMANDS - USER INFO
# ══════════════════════════════════════════════════════════════════════════════

BADGE_EMOJIS = {
    discord.UserFlags.staff: "Discord Employee",
    discord.UserFlags.partner: "Partnered Server Owner",
    discord.UserFlags.hypesquad: "HypeSquad Events",
    discord.UserFlags.bug_hunter: "Bug Hunter",
    discord.UserFlags.hypesquad_bravery: "HypeSquad Bravery",
    discord.UserFlags.hypesquad_brilliance: "HypeSquad Brilliance",
    discord.UserFlags.hypesquad_balance: "HypeSquad Balance",
    discord.UserFlags.early_supporter: "Early Supporter",
    discord.UserFlags.team_user: "Team User",
    discord.UserFlags.system: "System",
    discord.UserFlags.bug_hunter_level_2: "Bug Hunter Level 2",
    discord.UserFlags.verified_bot: "Verified Bot",
    discord.UserFlags.verified_bot_developer: "Verified Bot Developer",
    discord.UserFlags.discord_certified_moderator: "Certified Moderator",
    discord.UserFlags.bot_http_interactions: "Bot HTTP Interactions",
    discord.UserFlags.active_developer: "Active Developer",
}


def get_user_badges(user):
    """Get user's badge/flag names"""
    badges = []
    for flag, name in BADGE_EMOJIS.items():
        if user.public_flags & flag:
            badges.append(name)
    return badges


@bot.tree.command(name="userinfo", description="View detailed info about a user")
@app_commands.describe(member="Member to inspect")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    
    # Fetch user for banner and other info
    user = await bot.fetch_user(member.id)
    
    e = discord.Embed(title=f"👤 {member}", color=member.color if member.color != discord.Color.default() else 0x5865F2)
    e.set_thumbnail(url=member.display_avatar.url)
    
    # Basic Info
    e.add_field(name="ID", value=member.id, inline=True)
    e.add_field(name="Username", value=str(member), inline=True)
    
    # Display name / nickname
    e.add_field(name="Global Name", value=member.global_name or member.name, inline=True)
    e.add_field(name="Nickname", value=member.nick or "None", inline=True)
    
    # Avatar info
    avatar_types = []
    if member.guild_avatar:
        avatar_types.append("Server")
    avatar_types.append("Global")
    e.add_field(name="Avatar", value=" + ".join(avatar_types), inline=True)
    
    # Account dates
    created_ts = int(member.created_at.timestamp())
    account_age = datetime.datetime.utcnow() - member.created_at.replace(tzinfo=None)
    years = int(account_age.days / 365)
    days = account_age.days % 365
    age_str = f"{years}y {days}d" if years > 0 else f"{days}d"
    e.add_field(name="Account Created", value=f"<t:{created_ts}:R>\n({age_str} old)", inline=True)
    
    if member.joined_at:
        joined_ts = int(member.joined_at.timestamp())
        join_age = datetime.datetime.utcnow() - member.joined_at.replace(tzinfo=None)
        j_years = int(join_age.days / 365)
        j_days = join_age.days % 365
        j_str = f"{j_years}y {j_days}d" if j_years > 0 else f"{j_days}d"
        e.add_field(name="Joined Server", value=f"<t:{joined_ts}:R>\n({j_str} ago)", inline=True)
    
    # Roles
    roles = [r for r in member.roles if r.name != "@everyone"]
    hoisted_role = member.top_role if member.top_role.name != "@everyone" else None
    e.add_field(name=f"Roles ({len(roles)})", value=" ".join([r.mention for r in sorted(roles, key=lambda r: r.position, reverse=True)[:10]]) or "None", inline=False)
    if hoisted_role:
        e.add_field(name="Hoisted Role", value=hoisted_role.mention, inline=True)
    
    # Status
    status_emoji = {"online": "🟢", "idle": "🟡", "dnd": "🔴", "offline": "⚫"}
    e.add_field(name="Status", value=f"{status_emoji.get(str(member.status), '⚪')} {str(member.status).title()}", inline=True)
    
    # Voice State
    if member.voice and member.voice.channel:
        vc_info = f"🔊 {member.voice.channel.name}"
        if member.voice.self_mute:
            vc_info += " (🔇 Self-muted)"
        if member.voice.self_deaf:
            vc_info += " ( headphone)"
        if member.voice.self_stream:
            vc_info += " (📺 Streaming)"
        if member.voice.self_video:
            vc_info += " (📹 Video)"
        e.add_field(name="Voice Channel", value=vc_info, inline=True)
    
    # Boost Status
    if member.premium_since:
        boost_ts = int(member.premium_since.timestamp())
        e.add_field(name="Boosting Since", value=f"<t:{boost_ts}:R>", inline=True)
    
    # Timeout Status
    if member.timed_out_until:
        timeout_ts = int(member.timed_out_until.timestamp())
        e.add_field(name="⏰ Timed Out Until", value=f"<t:{timeout_ts}:R>", inline=True)
    
    # Pending membership
    if member.pending:
        e.add_field(name="Pending", value="Yes (not verified)", inline=True)
    
    # Key permissions
    key_perms = []
    if member.guild_permissions.administrator:
        key_perms.append("Administrator")
    if member.guild_permissions.manage_guild:
        key_perms.append("Manage Server")
    if member.guild_permissions.moderate_members:
        key_perms.append("Moderate Members")
    if member.guild_permissions.manage_roles:
        key_perms.append("Manage Roles")
    if member.guild_permissions.manage_channels:
        key_perms.append("Manage Channels")
    if member.guild_permissions.manage_webhooks:
        key_perms.append("Manage Webhooks")
    if member.guild_permissions.manage_emojis:
        key_perms.append("Manage Emojis")
    if member.guild_permissions.view_audit_log:
        key_perms.append("View Audit Log")
    if member.guild_permissions.mention_everyone:
        key_perms.append("Mention Everyone")
    
    if key_perms:
        e.add_field(name="Key Permissions", value=", ".join(key_perms[:6]), inline=False)
    
    # Badges/Flags
    badges = get_user_badges(user)
    if badges:
        e.add_field(name="Badges", value=", ".join(badges[:6]), inline=False)
    
    # Banner
    if user.banner:
        e.set_image(url=user.banner.url)
    
    # Mutual servers
    mutual_servers = len(member.mutual_guilds) if hasattr(member, 'mutual_guilds') else "?"
    e.set_footer(text=f"Mutual Servers: {mutual_servers}")
    
    await interaction.response.send_message(embed=e)


# Aliases for userinfo






# ══════════════════════════════════════════════════════════════════════════════
# INFORMATION COMMANDS - SERVER INFO
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="serverinfo", description="View detailed server info")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    
    e = discord.Embed(title=f"🏠 {g.name}", color=0x5865F2)
    
    # Server icons and images
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    if g.banner:
        e.set_image(url=g.banner.url)
    
    # Basic info
    e.add_field(name="Server ID", value=g.id, inline=True)
    e.add_field(name="Owner", value=g.owner.mention if g.owner else "Unknown", inline=True)
    e.add_field(name="Created", value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
    
    # Member counts
    total = g.member_count or len(g.members) or 0
    members_list = list(g.members) if g.members else []
    humans = len([m for m in members_list if not m.bot])
    bots = len([m for m in members_list if m.bot])
    online = len([m for m in members_list if m.status != discord.Status.offline])
    idle = len([m for m in members_list if m.status == discord.Status.idle])
    dnd = len([m for m in members_list if m.status == discord.Status.dnd])
    
    e.add_field(name="Members", value=f"**{total}** Total\n👤 {humans} Humans\n🤖 {bots} Bots", inline=True)
    e.add_field(name="Presence", value=f"🟢 {online} Online\n🟡 {idle} Idle\n🔴 {dnd} DND", inline=True)
    
    # Channel counts
    text_channels = len(g.text_channels)
    voice_channels = len(g.voice_channels)
    categories = len(g.categories)
    threads = len(g.threads)
    stage_channels = len(g.stage_channels)
    forum_channels = len([c for c in g.channels if isinstance(c, discord.ForumChannel)])
    
    e.add_field(name="Channels", value=f"💬 {text_channels} Text\n🔊 {voice_channels} Voice\n📁 {categories} Categories\n🧵 {threads} Threads", inline=True)
    
    # Other counts
    e.add_field(name="Roles", value=len(g.roles), inline=True)
    
    # Emoji counts
    normal_emojis = len([e for e in g.emojis if not e.animated])
    animated_emojis = len([e for e in g.emojis if e.animated])
    e.add_field(name="Emojis", value=f"😀 {normal_emojis} Normal\n🎬 {animated_emojis} Animated\n📊 {len(g.emojis)}/{g.emoji_limit}", inline=True)
    
    # Stickers
    e.add_field(name="Stickers", value=f"{len(g.stickers)}/{g.sticker_limit}", inline=True)
    
    # Boost info
    boost_level = g.premium_tier
    boost_count = g.premium_subscription_count
    boosters = len([m for m in members_list if m.premium_since])
    e.add_field(name="Boost Level", value=f"Level {boost_level}\n⚡ {boost_count} Boosts\n👑 {boosters} Boosters", inline=True)
    
    # Security settings
    verification_levels = {"none": "None", "low": "Low", "medium": "Medium", "high": "High", "highest": "Highest"}
    filter_levels = {"disabled": "Disabled", "no_role": "No Role", "all_members": "All Members"}
    nsfw_levels = {"default": "Default", "explicit": "Explicit", "safe": "Safe", "age_restricted": "Age Restricted"}
    
    e.add_field(name="Verification", value=verification_levels.get(str(g.verification_level), str(g.verification_level)), inline=True)
    e.add_field(name="Content Filter", value=filter_levels.get(str(g.explicit_content_filter), str(g.explicit_content_filter)), inline=True)
    e.add_field(name="NSFW Level", value=nsfw_levels.get(str(g.nsfw_level), str(g.nsfw_level)), inline=True)
    
    # Notification settings
    notif_settings = "All Messages" if g.default_notifications == discord.NotificationLevel.all_messages else "Only @mentions"
    e.add_field(name="Default Notifications", value=notif_settings, inline=True)
    
    # AFK settings
    if g.afk_channel:
        afk_timeout = f"{g.afk_timeout // 60} min" if g.afk_timeout >= 60 else f"{g.afk_timeout} sec"
        e.add_field(name="AFK Channel", value=f"{g.afk_channel.mention}\n⏱️ {afk_timeout} timeout", inline=True)
    
    # System channels
    system_channels = []
    if g.system_channel:
        system_channels.append(f"System: {g.system_channel.mention}")
    if g.rules_channel:
        system_channels.append(f"Rules: {g.rules_channel.mention}")
    if g.public_updates_channel:
        system_channels.append(f"Updates: {g.public_updates_channel.mention}")
    if system_channels:
        e.add_field(name="System Channels", value="\n".join(system_channels), inline=True)
    
    # Vanity URL
    if g.vanity_url:
        e.add_field(name="Vanity URL", value=f"`{g.vanity_url}`", inline=True)
    
    # Preferred locale
    if g.preferred_locale:
        e.add_field(name="Preferred Locale", value=g.preferred_locale, inline=True)
    
    # Description
    if g.description:
        e.add_field(name="Description", value=g.description[:200], inline=False)
    
    # Server features
    features = [f.replace("_", " ").title() for f in g.features[:8]]
    if features:
        e.add_field(name="Features", value=", ".join(features), inline=False)
    
    await interaction.response.send_message(embed=e)


# Aliases for serverinfo






# ══════════════════════════════════════════════════════════════════════════════
# INFORMATION COMMANDS - AVATAR & BANNER
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="avatar", description="View a user's avatar")
@app_commands.describe(member="Member to view", server="Show server avatar (if set)")
async def avatar(interaction: discord.Interaction, member: discord.Member = None, server: bool = False):
    member = member or interaction.user
    
    # Determine which avatar to show
    if server and member.guild_avatar:
        avatar_url = member.guild_avatar.url
        title = f"🖼️ {member.display_name}'s Server Avatar"
    else:
        avatar_url = member.display_avatar.url
        title = f"🖼️ {member.display_name}'s Avatar"
    
    e = discord.Embed(title=title, color=member.color if member.color != discord.Color.default() else 0x5865F2)
    e.set_image(url=avatar_url)
    
    # Add links for both avatars if server avatar exists
    if member.guild_avatar:
        e.add_field(name="Server Avatar", value=f"[Link]({member.guild_avatar.url})", inline=True)
    e.add_field(name="Global Avatar", value=f"[Link]({member.display_avatar.url})", inline=True)
    
    # Add formats
    formats = []
    if avatar_url.endswith('.png') or '?' in avatar_url:
        base = avatar_url.split('?')[0]
        formats.append(f"[PNG]({base}?size=1024)")
        formats.append(f"[JPG]({base.replace('.png', '.jpg')}?size=1024)")
        formats.append(f"[WEBP]({base.replace('.png', '.webp')}?size=1024)")
    if member.display_avatar.is_animated():
        formats.append(f"[GIF]({member.display_avatar.url})")
    
    if formats:
        e.add_field(name="Formats", value=" | ".join(formats[:4]), inline=False)
    
    await interaction.response.send_message(embed=e)


# Aliases for avatar




@bot.tree.command(name="banner", description="View a user's banner")
@app_commands.describe(member="Member to view")
async def banner(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    
    # Fetch user for banner
    user = await bot.fetch_user(member.id)
    
    if not user.banner:
        await interaction.response.send_message(f"❌ **{member.display_name}** doesn't have a banner.", ephemeral=True)
        return
    
    e = discord.Embed(title=f"🖼️ {member.display_name}'s Banner", color=user.accent_color or 0x5865F2)
    e.set_image(url=user.banner.url)
    e.add_field(name="URL", value=f"[Link]({user.banner.url})", inline=False)
    
    if user.accent_color:
        e.add_field(name="Accent Color", value=f"#{user.accent_color}", inline=True)
    
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="icon", description="View server icon")
async def icon(interaction: discord.Interaction):
    g = interaction.guild
    
    if not g.icon:
        await interaction.response.send_message("❌ This server doesn't have an icon.", ephemeral=True)
        return
    
    e = discord.Embed(title=f"🖼️ {g.name}'s Icon", color=0x5865F2)
    e.set_image(url=g.icon.url)
    e.add_field(name="URL", value=f"[Link]({g.icon.url})", inline=False)
    
    if g.icon.is_animated():
        e.add_field(name="Animated", value="Yes ✅", inline=True)
    
    await interaction.response.send_message(embed=e)




@bot.tree.command(name="splash", description="View server splash image")
async def splash(interaction: discord.Interaction):
    g = interaction.guild
    
    if not g.splash:
        await interaction.response.send_message("❌ This server doesn't have a splash image (invite background).", ephemeral=True)
        return
    
    e = discord.Embed(title=f"🖼️ {g.name}'s Splash", color=0x5865F2)
    e.set_image(url=g.splash.url)
    e.add_field(name="URL", value=f"[Link]({g.splash.url})", inline=False)
    
    await interaction.response.send_message(embed=e)


# ══════════════════════════════════════════════════════════════════════════════
# INFORMATION COMMANDS - ROLE INFO
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="roleinfo", description="View detailed role information")
@app_commands.describe(role="Role to view")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    e = discord.Embed(title=f"🏷️ {role.name}", color=role.color if role.color != discord.Color.default() else 0x5865F2)
    
    if role.icon:
        e.set_thumbnail(url=role.icon.url)
    elif role.unicode_emoji:
        e.set_thumbnail(url=f"https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/{ord(role.unicode_emoji):x}.png")
    
    # Basic info
    e.add_field(name="Role ID", value=role.id, inline=True)
    e.add_field(name="Color", value=f"#{role.color}" if role.color != discord.Color.default() else "Default", inline=True)
    e.add_field(name="Position", value=f"#{len(interaction.guild.roles) - role.position}", inline=True)
    
    # Member count
    e.add_field(name="Members", value=len(role.members), inline=True)
    e.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
    e.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
    
    # Managed status
    if role.managed:
        managed_info = "Yes"
        if role.tags:
            if role.tags.bot_id:
                bot = interaction.guild.get_member(role.tags.bot_id)
                managed_info += f" (Bot: {bot.name if bot else role.tags.bot_id})"
            elif role.tags.integration_id:
                managed_info += " (Integration)"
        e.add_field(name="Managed", value=managed_info, inline=True)
    else:
        e.add_field(name="Managed", value="No", inline=True)
    
    # Creation date
    e.add_field(name="Created", value=f"<t:{int(role.created_at.timestamp())}:R>", inline=False)
    
    # Permissions
    perms = role.permissions
    perm_list = []
    
    if perms.administrator:
        perm_list.append("**Administrator** (All Permissions)")
    else:
        if perms.manage_guild:
            perm_list.append("Manage Server")
        if perms.moderate_members:
            perm_list.append("Moderate Members")
        if perms.manage_roles:
            perm_list.append("Manage Roles")
        if perms.manage_channels:
            perm_list.append("Manage Channels")
        if perms.manage_webhooks:
            perm_list.append("Manage Webhooks")
        if perms.manage_emojis:
            perm_list.append("Manage Emojis")
        if perms.view_audit_log:
            perm_list.append("View Audit Log")
        if perms.mention_everyone:
            perm_list.append("Mention @everyone")
        if perms.kick_members:
            perm_list.append("Kick Members")
        if perms.ban_members:
            perm_list.append("Ban Members")
        if perms.manage_nicknames:
            perm_list.append("Manage Nicknames")
        if perms.change_nickname:
            perm_list.append("Change Nickname")
        if perms.view_channel:
            perm_list.append("View Channels")
        if perms.send_messages:
            perm_list.append("Send Messages")
        if perms.manage_messages:
            perm_list.append("Manage Messages")
        if perms.embed_links:
            perm_list.append("Embed Links")
        if perms.attach_files:
            perm_list.append("Attach Files")
        if perms.read_message_history:
            perm_list.append("Read History")
        if perms.add_reactions:
            perm_list.append("Add Reactions")
        if perms.connect:
            perm_list.append("Connect to Voice")
        if perms.speak:
            perm_list.append("Speak in Voice")
        if perms.mute_members:
            perm_list.append("Mute Members")
        if perms.deafen_members:
            perm_list.append("Deafen Members")
        if perms.move_members:
            perm_list.append("Move Members")
        if perms.stream:
            perm_list.append("Stream")
        if perms.priority_speaker:
            perm_list.append("Priority Speaker")
    
    if perm_list:
        e.add_field(name=f"Permissions ({len(perm_list)})", value=", ".join(perm_list[:15]), inline=False)
    
    # Permission bitfield
    e.add_field(name="Permission Bitfield", value=f"`{perms.value}`", inline=False)
    
    await interaction.response.send_message(embed=e)




@bot.tree.command(name="roles", description="List all server roles with member counts")
async def roles(interaction: discord.Interaction):
    g = interaction.guild
    
    e = discord.Embed(title=f"🏷️ Server Roles ({len(g.roles) - 1})", color=0x5865F2)
    
    # Sort roles by position (highest first)
    sorted_roles = sorted([r for r in g.roles if r.name != "@everyone"], key=lambda r: r.position, reverse=True)
    
    role_list = []
    for role in sorted_roles[:25]:
        role_list.append(f"{role.mention} — {len(role.members)} members")
    
    e.description = "\n".join(role_list) or "No roles"
    
    if len(sorted_roles) > 25:
        e.set_footer(text=f"Showing 25 of {len(sorted_roles)} roles")
    
    await interaction.response.send_message(embed=e)




@bot.tree.command(name="inrole", description="Show members with a specific role")
@app_commands.describe(role="Role to check")
async def inrole(interaction: discord.Interaction, role: discord.Role):
    members = role.members
    
    e = discord.Embed(title=f"👥 Members with {role.name}", color=role.color if role.color != discord.Color.default() else 0x5865F2)
    e.description = f"**{len(members)}** member{'s' if len(members) != 1 else ''} have this role"
    
    if members:
        # Paginate if too many members
        member_list = []
        for m in sorted(members, key=lambda x: x.display_name)[:50]:
            member_list.append(f"• {m.mention} ({m})")
        e.add_field(name="Members", value="\n".join(member_list), inline=False)
        
        if len(members) > 50:
            e.set_footer(text=f"Showing 50 of {len(members)} members")
    else:
        e.add_field(name="Members", value="No members have this role", inline=False)
    
    await interaction.response.send_message(embed=e)




# ══════════════════════════════════════════════════════════════════════════════
# INFORMATION COMMANDS - LOOKUP & HISTORY
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="lookup", description="Look up a user by ID (even if not in server)")
@app_commands.describe(user_id="User ID to lookup")
async def lookup(interaction: discord.Interaction, user_id: str):
    await interaction.response.defer(ephemeral=True)
    
    try:
        user_id = int(user_id)
        user = await bot.fetch_user(user_id)
        
        e = discord.Embed(title=f"🔍 User Lookup", color=0x5865F2)
        e.set_thumbnail(url=user.display_avatar.url)
        
        # Basic info
        e.add_field(name="ID", value=user.id, inline=True)
        e.add_field(name="Username", value=str(user), inline=True)
        e.add_field(name="Global Name", value=user.global_name or user.name, inline=True)
        
        # Account info
        created_ts = int(user.created_at.timestamp())
        account_age = datetime.datetime.utcnow() - user.created_at.replace(tzinfo=None)
        years = int(account_age.days / 365)
        days = account_age.days % 365
        age_str = f"{years}y {days}d" if years > 0 else f"{days}d"
        e.add_field(name="Account Created", value=f"<t:{created_ts}:R>\n({age_str} old)", inline=True)
        
        # Bot check
        if user.bot:
            e.add_field(name="Bot", value="Yes 🤖", inline=True)
        if user.system:
            e.add_field(name="System", value="Yes ⚙️", inline=True)
        
        # Badges
        badges = get_user_badges(user)
        if badges:
            e.add_field(name="Badges", value=", ".join(badges), inline=False)
        
        # Banner
        if user.banner:
            e.set_image(url=user.banner.url)
            e.add_field(name="Banner", value=f"[Link]({user.banner.url})", inline=True)
        
        if user.accent_color:
            e.add_field(name="Accent Color", value=f"#{user.accent_color}", inline=True)
        
        # Check if in server
        member = interaction.guild.get_member(user_id)
        if member:
            e.add_field(name="In Server", value="Yes ✅", inline=True)
            if member.joined_at:
                e.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
            if member.nick:
                e.add_field(name="Nickname", value=member.nick, inline=True)
        else:
            e.add_field(name="In Server", value="No ❌", inline=True)
        
        await interaction.followup.send(embed=e, ephemeral=True)
    except discord.NotFound:
        await interaction.followup.send("❌ User not found. The ID may be invalid.", ephemeral=True)
    except Exception as ex:
        await interaction.followup.send(f"❌ Error: {ex}", ephemeral=True)


@bot.tree.command(name="history", description="View moderation history for a user")
@app_commands.describe(member="Member to check")
@is_mod()
async def history(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        all_cases = await get_cases(session, interaction.guild)
        warnings = await get_warnings(session, interaction.guild)
    
    # Filter cases for this user
    user_cases = [c for c in all_cases if c.get("target_id") == str(member.id)]
    user_warnings = warnings.get(str(member.id), [])
    
    e = discord.Embed(title=f"📋 Moderation History: {member}", color=0x5865F2)
    e.set_thumbnail(url=member.display_avatar.url)
    
    # Summary
    action_counts = {}
    for c in user_cases:
        action = c.get("action", "unknown")
        action_counts[action] = action_counts.get(action, 0) + 1
    
    summary = []
    for action, count in action_counts.items():
        summary.append(f"**{action.title()}**: {count}")
    e.add_field(name="Summary", value="\n".join(summary) or "No actions", inline=True)
    
    # Warnings
    e.add_field(name="Warnings", value=str(len(user_warnings)), inline=True)
    
    # Recent cases
    if user_cases:
        recent = user_cases[-5:]
        case_text = []
        for c in reversed(recent):
            ts = int(datetime.datetime.fromisoformat(c['timestamp']).timestamp())
            case_text.append(f"**#{c['id']}** {c['action'].upper()} - {c['reason'][:30]}\n<t:{ts}:R> by {c['mod_name']}")
        e.add_field(name="Recent Cases", value="\n".join(case_text), inline=False)
    else:
        e.add_field(name="Recent Cases", value="No cases found", inline=False)
    
    await interaction.followup.send(embed=e, ephemeral=True)




# ══════════════════════════════════════════════════════════════════════════════
# CASE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="cases", description="View recent mod cases")
@app_commands.describe(user="Filter by user (optional)")
@is_mod()
async def cases(interaction: discord.Interaction, user: discord.Member = None):
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        all_cases = await get_cases(session, interaction.guild)
    
    if user:
        all_cases = [c for c in all_cases if c.get("target_id") == str(user.id)]
    
    recent = all_cases[-10:]
    e = discord.Embed(title="📋 Recent mod cases", color=0x5865F2)
    
    if not recent:
        e.description = "No cases found."
    else:
        for c in reversed(recent):
            timestamp = int(datetime.datetime.fromisoformat(c['timestamp']).timestamp())
            e.add_field(
                name=f"#{c['id']} — {c['action'].upper()} by {c['mod_name']}",
                value=f"**Target:** {c['target_name']}\n**Reason:** {c['reason']}\n<t:{timestamp}:R>",
                inline=False,
            )
    
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="case", description="View or manage a specific case")
@app_commands.describe(case_id="Case ID", action="Action to take", new_reason="New reason (for edit)")
@app_commands.choices(action=[
    app_commands.Choice(name="view", value="view"),
    app_commands.Choice(name="edit", value="edit"),
    app_commands.Choice(name="delete", value="delete"),
    app_commands.Choice(name="pardon", value="pardon"),
])
@is_mod()
async def case_cmd(interaction: discord.Interaction, case_id: int, action: str = "view", new_reason: str = None):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        all_cases = await get_cases(session, interaction.guild)
        
        case = next((c for c in all_cases if c.get("id") == case_id), None)
        
        if not case:
            await interaction.followup.send(f"❌ Case #{case_id} not found.", ephemeral=True)
            return
        
        if action == "view":
            e = discord.Embed(title=f"📋 Case #{case_id}", color=0x5865F2)
            for key, value in case.items():
                if key == "timestamp":
                    value = f"<t:{int(datetime.datetime.fromisoformat(value).timestamp())}:R>"
                e.add_field(name=key.replace("_", " ").title(), value=str(value), inline=True)
            await interaction.followup.send(embed=e, ephemeral=True)
        
        elif action == "edit" and new_reason:
            case["reason"] = new_reason
            await save_cases(session, interaction.guild, all_cases)
            await interaction.followup.send(f"✅ Case #{case_id} reason updated to: {new_reason}", ephemeral=True)
        
        elif action == "delete":
            all_cases.remove(case)
            await save_cases(session, interaction.guild, all_cases)
            await interaction.followup.send(f"✅ Case #{case_id} deleted.", ephemeral=True)
        
        elif action == "pardon":
            case["active"] = False
            case["pardoned"] = True
            case["pardoned_by"] = str(interaction.user)
            case["pardoned_at"] = datetime.datetime.utcnow().isoformat()
            await save_cases(session, interaction.guild, all_cases)
            await interaction.followup.send(f"✅ Case #{case_id} has been pardoned.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# HONEYPOT
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
            cfg = await get_config(session, interaction.guild)
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
        all_g, sha = await gh_read(session, FILE_GIVEAWAYS, guild_branch_from_id(guild_id))
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
        await gh_write(session, FILE_GIVEAWAYS, all_g, sha, "Vortex: new giveaway", guild_branch_from_id(guild_id))
    await interaction.followup.send(f"✅ Giveaway started in {channel.mention}!", ephemeral=True)


@tasks.loop(minutes=1)
async def check_giveaways():
    now = datetime.datetime.utcnow()
    async with aiohttp.ClientSession() as session:
        all_g, sha = await gh_read(session, FILE_GIVEAWAYS, guild_branch_from_id(guild_id))
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
                            if users:
                                chosen = random.sample(users, min(gw["winners"], len(users)))
                                winners_text = " ".join(u.mention for u in chosen)
                                await ch.send(f"🎉 Giveaway ended! Winners: {winners_text}\nPrize: **{gw['prize']}**")
                            else:
                                await ch.send("🎉 Giveaway ended but no one entered.")
                    except Exception:
                        pass
        if changed:
            await gh_write(session, FILE_GIVEAWAYS, all_g, sha, "Vortex: end giveaway", guild_branch_from_id(guild_id))


# ══════════════════════════════════════════════════════════════════════════════
# LEVELING
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="rank", description="Check your rank")
@app_commands.describe(member="Member to check (default: yourself)")
async def rank(interaction: discord.Interaction, member: discord.Member = None):
    member   = member or interaction.user
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        levels = await get_levels(session, interaction.guild)
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
        levels = await get_levels(session, interaction.guild)
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
# SETUP COMMANDS — /setup group
# ══════════════════════════════════════════════════════════════════════════════

class SetupGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="setup", description="Server setup commands")

setup_group = SetupGroup()
bot.tree.add_command(setup_group)

@setup_group.command(name="modlog", description="Set the mod log channel")
@app_commands.describe(channel="Channel for mod logs")
async def setup_modlog(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg["mod_log"] = str(channel.id)
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Mod log set to {channel.mention}", ephemeral=True)

@setup_group.command(name="welcome", description="Set the welcome channel and message")
@app_commands.describe(channel="Welcome channel", message="Welcome message ({user} and {server} placeholders)")
async def setup_welcome(interaction: discord.Interaction, channel: discord.TextChannel, message: str = None):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg["welcome_channel"] = str(channel.id)
        if message: cfg["welcome_message"] = message
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Welcome channel set to {channel.mention}", ephemeral=True)

@setup_group.command(name="tickets", description="Set the ticket category")
@app_commands.describe(category="Category for ticket channels")
async def setup_tickets(interaction: discord.Interaction, category: discord.CategoryChannel):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg["ticket_category"] = str(category.id)
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Ticket category set to **{category.name}**", ephemeral=True)

@setup_group.command(name="muted", description="Set the muted role")
@app_commands.describe(role="Muted role")
async def setup_muted(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg["muted_role"] = str(role.id)
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Muted role set to {role.mention}", ephemeral=True)

@setup_group.command(name="quarantine", description="Set the quarantine role")
@app_commands.describe(role="Quarantine role")
async def setup_quarantine_cmd(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg["quarantine_role"] = str(role.id)
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Quarantine role set to {role.mention}", ephemeral=True)

@setup_group.command(name="modrole", description="Add a moderator role")
@app_commands.describe(role="Role to add as moderator")
async def setup_modrole(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        mod_data = await get_mod_roles(session, interaction.guild)
        if str(role.id) not in mod_data["mod_roles"]: mod_data["mod_roles"].append(str(role.id))
        await save_mod_roles(session, interaction.guild, mod_data)
    await interaction.followup.send(f"✅ {role.mention} is now a moderator role.", ephemeral=True)

@setup_group.command(name="adminrole", description="Add an admin role")
@app_commands.describe(role="Role to add as admin")
async def setup_adminrole(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        mod_data = await get_mod_roles(session, interaction.guild)
        if str(role.id) not in mod_data["admin_roles"]: mod_data["admin_roles"].append(str(role.id))
        await save_mod_roles(session, interaction.guild, mod_data)
    await interaction.followup.send(f"✅ {role.mention} is now an admin role.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# AUTOMOD COMMANDS — /automod group
# ══════════════════════════════════════════════════════════════════════════════

class AutomodGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="automod", description="Automod configuration")

automod_group = AutomodGroup()
bot.tree.add_command(automod_group)

@automod_group.command(name="setup", description="Enable or disable an automod rule")
@app_commands.describe(rule="Rule to configure", enabled="Enable or disable")
@app_commands.choices(rule=[
    app_commands.Choice(name="spam",     value="spam"),
    app_commands.Choice(name="caps",     value="caps"),
    app_commands.Choice(name="links",    value="links"),
    app_commands.Choice(name="words",    value="words"),
    app_commands.Choice(name="invites",  value="invites"),
    app_commands.Choice(name="mentions", value="mentions"),
    app_commands.Choice(name="emojis",   value="emojis"),
    app_commands.Choice(name="newlines", value="newlines"),
    app_commands.Choice(name="zalgo",    value="zalgo"),
])
async def automod_setup(interaction: discord.Interaction, rule: str, enabled: bool):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        if "automod" not in cfg: cfg["automod"] = DEFAULT_CONFIG["automod"].copy()
        if rule not in cfg["automod"]: cfg["automod"][rule] = {"enabled": False}
        cfg["automod"][rule]["enabled"] = enabled
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"Automod **{rule}** {'enabled ✅' if enabled else 'disabled ❌'}", ephemeral=True)

@automod_group.command(name="words", description="Add/remove words from the blacklist")
@app_commands.describe(action="add or remove", word="Word")
@app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove")])
async def automod_words(interaction: discord.Interaction, action: str, word: str):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        if "automod" not in cfg: cfg["automod"] = DEFAULT_CONFIG["automod"].copy()
        words = cfg["automod"].setdefault("words", {}).setdefault("blacklist", [])
        if action == "add" and word not in words: words.append(word.lower())
        elif action == "remove" and word.lower() in words: words.remove(word.lower())
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Word `{word}` {action}ed.", ephemeral=True)

@automod_group.command(name="links", description="Add/remove domains from link whitelist")
@app_commands.describe(action="add or remove", domain="Domain")
@app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove")])
async def automod_links(interaction: discord.Interaction, action: str, domain: str):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        if "automod" not in cfg: cfg["automod"] = DEFAULT_CONFIG["automod"].copy()
        whitelist = cfg["automod"].setdefault("links", {}).setdefault("whitelist", [])
        if action == "add" and domain not in whitelist: whitelist.append(domain.lower())
        elif action == "remove" and domain.lower() in whitelist: whitelist.remove(domain.lower())
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Domain `{domain}` {action}ed.", ephemeral=True)

@bot.tree.command(name="ghostping", description="Check for recent ghost pings")
@is_mod()
async def ghostping(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    recent = _ghost_pings.get(guild_id, [])[-5:]
    
    e = discord.Embed(title="👻 Recent Ghost Pings", color=0xFEE75C)
    
    if not recent:
        e.description = "No recent ghost pings detected."
    else:
        for i, gp in enumerate(reversed(recent), 1):
            e.add_field(
                name=f"#{i} - {gp['author']}",
                value=f"Channel: {gp['channel']}\nMentions: {gp['mentions']}\n[Deleted message]",
                inline=False
            )
    
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="vortex", description="Show Vortex bot info")
async def vortex_info(interaction: discord.Interaction):
    e = discord.Embed(
        title="🌀 Vortex",
        description="A powerful moderation & community bot.",
        color=0x5865F2,
    )
    e.add_field(name="**Moderation**",  value="ban, hackban, softban, tempban, kick, mute, warn, purge, nuke", inline=False)
    e.add_field(name="**Lockdown**",     value="lock, unlock, lockall, slowmode, raidmode, panic", inline=False)
    e.add_field(name="**Voice Mod**",    value="vckick, vcmove, vcmute, vcdeafen", inline=False)
    e.add_field(name="**Info Commands**", value="userinfo, serverinfo, avatar, banner, roleinfo, lookup", inline=False)
    e.add_field(name="**Automod**",      value="spam, caps, links, words, invites, mentions, emojis, zalgo", inline=False)
    e.add_field(name="**Other**",        value="tickets, giveaways, leveling, polls, reaction roles, honeypot", inline=False)
    e.set_footer(text="Use /setup modlog to get started!")
    await interaction.response.send_message(embed=e)


# ══════════════════════════════════════════════════════════════════════════════
# HONEYPOT — /honeypot group
# ══════════════════════════════════════════════════════════════════════════════

class HoneypotGroup(app_commands.Group):
    def __init__(self): super().__init__(name="honeypot", description="Honeypot channel management")
honeypot_group = HoneypotGroup()
bot.tree.add_command(honeypot_group)

@honeypot_group.command(name="add", description="Mark a channel as a honeypot trap")
@app_commands.describe(channel="Channel to mark as honeypot", action="Action when triggered")
@app_commands.choices(action=[
    app_commands.Choice(name="kick",value="kick"),
    app_commands.Choice(name="ban", value="ban"),
    app_commands.Choice(name="mute",value="mute"),
])
async def honeypot_add(interaction: discord.Interaction, channel: discord.TextChannel, action: str = "kick"):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        all_hp, sha = await gh_read(session, FILE_HONEYPOT, guild_branch_from_id(guild_id))
        if not all_hp: all_hp = {}
        if guild_id not in all_hp: all_hp[guild_id] = {}
        all_hp[guild_id][str(channel.id)] = {"action": action}
        await gh_write(session, FILE_HONEYPOT, all_hp, sha, f"Vortex: add honeypot {channel.id}", guild_branch_from_id(guild_id))
    await interaction.followup.send(f"🍯 {channel.mention} is now a honeypot (action: {action})", ephemeral=True)

@honeypot_group.command(name="remove", description="Remove a honeypot channel")
@app_commands.describe(channel="Channel to remove from honeypot")
async def honeypot_remove(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        all_hp, sha = await gh_read(session, FILE_HONEYPOT, guild_branch_from_id(guild_id))
        if all_hp and guild_id in all_hp:
            all_hp[guild_id].pop(str(channel.id), None)
            await gh_write(session, FILE_HONEYPOT, all_hp, sha, f"Vortex: remove honeypot {channel.id}", guild_branch_from_id(guild_id))
    await interaction.followup.send(f"✅ Removed honeypot from {channel.mention}", ephemeral=True)

@honeypot_group.command(name="protect", description="Add a role immune to honeypot")
@app_commands.describe(role="Role to protect")
async def honeypot_protect(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        protected = cfg.get("honeypot_protected_roles", [])
        if str(role.id) not in protected: protected.append(str(role.id))
        cfg["honeypot_protected_roles"] = protected
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ {role.mention} is now protected from honeypot.", ephemeral=True)

@honeypot_group.command(name="unprotect", description="Remove a role from honeypot protection")
@app_commands.describe(role="Role to unprotect")
async def honeypot_unprotect(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        protected = cfg.get("honeypot_protected_roles", [])
        if str(role.id) in protected: protected.remove(str(role.id))
        cfg["honeypot_protected_roles"] = protected
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ {role.mention} removed from honeypot protection.", ephemeral=True)

@honeypot_group.command(name="list", description="List all honeypot channels")
async def honeypot_list(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.moderate_members: await interaction.response.send_message("❌ Mod only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        all_hp, _ = await gh_read(session, FILE_HONEYPOT, guild_branch_from_id(guild_id))
    guild_hp = (all_hp or {}).get(guild_id, {})
    e = discord.Embed(title="🍯 Honeypot Channels", color=0xFF6B35)
    if not guild_hp: e.description = "No honeypot channels."
    else:
        for ch_id, data in guild_hp.items():
            ch = interaction.guild.get_channel(int(ch_id))
            e.add_field(name=ch.mention if ch else ch_id, value=f"Action: {data.get('action','kick')}", inline=True)
    await interaction.followup.send(embed=e, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# REACTION ROLES — /rxrole group
# ══════════════════════════════════════════════════════════════════════════════

class RxRoleGroup(app_commands.Group):
    def __init__(self): super().__init__(name="rxrole", description="Reaction role management")
rxrole_group = RxRoleGroup()
bot.tree.add_command(rxrole_group)

@rxrole_group.command(name="add", description="Add a reaction role to a message")
@app_commands.describe(message_id="Message ID", emoji="Emoji", role="Role to assign")
async def rxrole_add(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    key = f"{message_id}:{emoji}"
    async with aiohttp.ClientSession() as session:
        all_rx, sha = await gh_read(session, FILE_RXROLES, guild_branch_from_id(guild_id))
        if not all_rx: all_rx = {}
        if guild_id not in all_rx: all_rx[guild_id] = {}
        all_rx[guild_id][key] = str(role.id)
        await gh_write(session, FILE_RXROLES, all_rx, sha, f"Vortex: add rxrole {key}", guild_branch_from_id(guild_id))
    try:
        ch = interaction.channel
        msg = await ch.fetch_message(int(message_id))
        await msg.add_reaction(emoji)
    except: pass
    await interaction.followup.send(f"✅ Reaction role added: {emoji} → {role.mention}", ephemeral=True)

@rxrole_group.command(name="remove", description="Remove a reaction role")
@app_commands.describe(message_id="Message ID", emoji="Emoji")
async def rxrole_remove(interaction: discord.Interaction, message_id: str, emoji: str):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    key = f"{message_id}:{emoji}"
    async with aiohttp.ClientSession() as session:
        all_rx, sha = await gh_read(session, FILE_RXROLES, guild_branch_from_id(guild_id))
        if all_rx and guild_id in all_rx and key in all_rx[guild_id]:
            del all_rx[guild_id][key]
            await gh_write(session, FILE_RXROLES, all_rx, sha, f"Vortex: remove rxrole {key}", guild_branch_from_id(guild_id))
    await interaction.followup.send(f"✅ Reaction role removed.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# TICKET COMMANDS — /ticket group
# ══════════════════════════════════════════════════════════════════════════════

class TicketGroup(app_commands.Group):
    def __init__(self): super().__init__(name="ticket", description="Ticket system")
ticket_group = TicketGroup()
bot.tree.add_command(ticket_group)

@ticket_group.command(name="open", description="Create a support ticket")
@app_commands.describe(reason="Reason for ticket")
async def ticket_open(interaction: discord.Interaction, reason: str = None):
    await TicketOpenView().open_ticket(interaction, None)

@ticket_group.command(name="setup", description="Post the ticket open panel")
async def ticket_setup_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    e = discord.Embed(title="🎫 Support Tickets", description="Click the button below to open a ticket.", color=0x5865F2)
    await interaction.channel.send(embed=e, view=TicketOpenView())
    await interaction.response.send_message("✅ Ticket panel posted.", ephemeral=True)

@ticket_group.command(name="close", description="Close the current ticket")
@app_commands.describe(reason="Close reason")
async def ticket_close_cmd(interaction: discord.Interaction, reason: str = "No reason"):
    if not interaction.user.guild_permissions.moderate_members: await interaction.response.send_message("❌ Mod only.", ephemeral=True); return
    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True); return
    await interaction.response.send_message(f"🔒 Closing ticket... Reason: {reason}")
    await asyncio.sleep(3)
    await interaction.channel.delete(reason=reason)

@ticket_group.command(name="add", description="Add a user to the ticket")
@app_commands.describe(user="User to add")
async def ticket_add_cmd(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.moderate_members: await interaction.response.send_message("❌ Mod only.", ephemeral=True); return
    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True); return
    await interaction.channel.set_permissions(user, overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=True))
    await interaction.response.send_message(f"✅ Added {user.mention}")

@ticket_group.command(name="remove", description="Remove a user from the ticket")
@app_commands.describe(user="User to remove")
async def ticket_remove_cmd(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.moderate_members: await interaction.response.send_message("❌ Mod only.", ephemeral=True); return
    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True); return
    await interaction.channel.set_permissions(user, overwrite=None)
    await interaction.response.send_message(f"✅ Removed {user.mention}")

@ticket_group.command(name="rename", description="Rename the current ticket")
@app_commands.describe(name="New name")
async def ticket_rename_cmd(interaction: discord.Interaction, name: str):
    if not interaction.user.guild_permissions.moderate_members: await interaction.response.send_message("❌ Mod only.", ephemeral=True); return
    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True); return
    await interaction.channel.edit(name=f"ticket-{name.lower().replace(' ', '-')}")
    await interaction.response.send_message(f"✅ Renamed ticket.")


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOM COMMANDS / TAGS — /tag group
# ══════════════════════════════════════════════════════════════════════════════

class TagGroup(app_commands.Group):
    def __init__(self): super().__init__(name="tag", description="Custom tags / commands")
tag_group = TagGroup()
bot.tree.add_command(tag_group)

@tag_group.command(name="use", description="Use a tag")
@app_commands.describe(name="Tag name")
async def tag_use(interaction: discord.Interaction, name: str):
    guild_id = str(interaction.guild_id)
    name = name.lower().replace(" ", "_")
    async with aiohttp.ClientSession() as session:
        cmds = await get_custom_cmds(session, guild_id)
        if name in cmds:
            cmds[name]["uses"] = cmds[name].get("uses", 0) + 1
            await save_custom_cmds(session, guild_id, cmds)
            await interaction.response.send_message(cmds[name]["response"])
        else:
            await interaction.response.send_message(f"❌ Tag `{name}` not found.", ephemeral=True)

@tag_group.command(name="create", description="Create a tag")
@app_commands.describe(name="Tag name", response="Tag content")
async def tag_create(interaction: discord.Interaction, name: str, response: str):
    if not interaction.user.guild_permissions.moderate_members: await interaction.response.send_message("❌ Mod only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    name = name.lower().replace(" ", "_")
    async with aiohttp.ClientSession() as session:
        cmds = await get_custom_cmds(session, guild_id)
        cmds[name] = {"response": response, "created_by": str(interaction.user.id), "uses": 0}
        await save_custom_cmds(session, guild_id, cmds)
    await interaction.followup.send(f"✅ Tag `{name}` created.", ephemeral=True)

@tag_group.command(name="delete", description="Delete a tag")
@app_commands.describe(name="Tag name")
async def tag_delete(interaction: discord.Interaction, name: str):
    if not interaction.user.guild_permissions.moderate_members: await interaction.response.send_message("❌ Mod only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    name = name.lower().replace(" ", "_")
    async with aiohttp.ClientSession() as session:
        cmds = await get_custom_cmds(session, guild_id)
        if name not in cmds: await interaction.followup.send(f"❌ Tag not found.", ephemeral=True); return
        del cmds[name]
        await save_custom_cmds(session, guild_id, cmds)
    await interaction.followup.send(f"✅ Tag `{name}` deleted.", ephemeral=True)

@tag_group.command(name="list", description="List all tags")
async def tag_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        cmds = await get_custom_cmds(session, guild_id)
    e = discord.Embed(title="📝 Tags", color=0x5865F2)
    e.description = "\n".join(f"• `{n}` — uses: {d.get('uses',0)}" for n, d in cmds.items()) if cmds else "No tags."
    await interaction.followup.send(embed=e, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# VOICE — fold voice_lock/unlock/limit/bitrate into existing /voice
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# AUTOROLE — add bots/delay as subcommands via choices on existing /autorole
# Since /autorole is already a flat command, add two extra flat commands
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="stickyroles", description="Enable/disable sticky roles (re-assign on rejoin)")
@app_commands.describe(enabled="Enable sticky roles")
async def stickyroles_cmd(interaction: discord.Interaction, enabled: bool):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg["sticky_roles"] = enabled
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Sticky roles {'enabled' if enabled else 'disabled'}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# STARBOARD — /starboard group
# ══════════════════════════════════════════════════════════════════════════════

class StarboardGroup(app_commands.Group):
    def __init__(self): super().__init__(name="starboard", description="Starboard management")
starboard_group = StarboardGroup()
bot.tree.add_command(starboard_group)

@starboard_group.command(name="setup", description="Set up the starboard")
@app_commands.describe(channel="Starboard channel", emoji="Emoji (default ⭐)", threshold="Required reactions")
async def starboard_setup(interaction: discord.Interaction, channel: discord.TextChannel, emoji: str = "⭐", threshold: int = 3):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        sb = await get_starboard(session, guild_id)
        sb.update({"channel_id": str(channel.id), "emoji": emoji, "threshold": threshold, "enabled": True, "starred": {}})
        await save_starboard(session, guild_id, sb)
    await interaction.followup.send(f"✅ Starboard → {channel.mention} | {emoji} × {threshold}", ephemeral=True)

@starboard_group.command(name="disable", description="Disable the starboard")
async def starboard_disable(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        sb = await get_starboard(session, guild_id)
        sb["enabled"] = False
        await save_starboard(session, guild_id, sb)
    await interaction.followup.send("✅ Starboard disabled", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# LEVEL ROLES — /levelrole group
# ══════════════════════════════════════════════════════════════════════════════

class LevelRoleGroup(app_commands.Group):
    def __init__(self): super().__init__(name="levelrole", description="Level role rewards")
levelrole_group = LevelRoleGroup()
bot.tree.add_command(levelrole_group)

@levelrole_group.command(name="add", description="Add a role reward for a level")
@app_commands.describe(level="Level", role="Role to give")
async def levelrole_add(interaction: discord.Interaction, level: int, role: discord.Role):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        if "level_roles" not in cfg: cfg["level_roles"] = {}
        cfg["level_roles"][str(level)] = str(role.id)
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Level {level} → {role.mention}", ephemeral=True)

@levelrole_group.command(name="remove", description="Remove a level role reward")
@app_commands.describe(level="Level to remove")
async def levelrole_remove(interaction: discord.Interaction, level: int):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg.get("level_roles", {}).pop(str(level), None)
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Removed level {level} role reward.", ephemeral=True)

@levelrole_group.command(name="list", description="List all level role rewards")
async def levelrole_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
    level_roles = cfg.get("level_roles", {})
    e = discord.Embed(title="🎖 Level Role Rewards", color=0x5865F2)
    if not level_roles: e.description = "No level roles set."
    else:
        e.description = "\n".join(f"Level {lvl}: <@&{rid}>" for lvl, rid in sorted(level_roles.items(), key=lambda x: int(x[0])))
    await interaction.followup.send(embed=e, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# BIRTHDAY — /birthday group
# ══════════════════════════════════════════════════════════════════════════════

class BirthdayGroup(app_commands.Group):
    def __init__(self): super().__init__(name="birthday", description="Birthday system")
birthday_group = BirthdayGroup()
bot.tree.add_command(birthday_group)

@birthday_group.command(name="set", description="Set your birthday")
@app_commands.describe(day="Day (1-31)", month="Month (1-12)")
async def birthday_set(interaction: discord.Interaction, day: int, month: int):
    if day < 1 or day > 31 or month < 1 or month > 12:
        await interaction.response.send_message("❌ Invalid date.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        bdays = await get_birthdays(session, guild_id)
        bdays[str(interaction.user.id)] = {"day": day, "month": month}
        await save_birthdays(session, guild_id, bdays)
    await interaction.followup.send(f"🎂 Birthday set: **{day}/{month}**", ephemeral=True)

@birthday_group.command(name="remove", description="Remove your birthday")
async def birthday_remove(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        bdays = await get_birthdays(session, guild_id)
        bdays.pop(str(interaction.user.id), None)
        await save_birthdays(session, guild_id, bdays)
    await interaction.followup.send("✅ Birthday removed.", ephemeral=True)

@birthday_group.command(name="role", description="Set birthday announcement role")
@app_commands.describe(role="Role to assign on birthday")
async def birthday_role(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        bdays = await get_birthdays(session, guild_id)
        bdays["birthday_role"] = str(role.id)
        await save_birthdays(session, guild_id, bdays)
    await interaction.followup.send(f"✅ Birthday role → {role.mention}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# REMINDERS — fold reminder_cancel into /reminders action param
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULED TASKS — /schedule group
# ══════════════════════════════════════════════════════════════════════════════

class ScheduleGroup(app_commands.Group):
    def __init__(self): super().__init__(name="schedule", description="Scheduled actions")
schedule_group = ScheduleGroup()
bot.tree.add_command(schedule_group)

@schedule_group.command(name="add", description="Schedule a moderation action")
@app_commands.describe(action="Action", target="Target user/channel ID", time="Time (e.g. 1h, 1d)", reason="Reason")
@app_commands.choices(action=[
    app_commands.Choice(name="ban",    value="ban"),
    app_commands.Choice(name="unban",  value="unban"),
    app_commands.Choice(name="mute",   value="mute"),
    app_commands.Choice(name="unmute", value="unmute"),
    app_commands.Choice(name="kick",   value="kick"),
    app_commands.Choice(name="lock",   value="lock"),
    app_commands.Choice(name="unlock", value="unlock"),
])
async def schedule_add(interaction: discord.Interaction, action: str, target: str, time: str, reason: str = "Scheduled action"):
    if not interaction.user.guild_permissions.moderate_members: await interaction.response.send_message("❌ Mod only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    td = parse_duration(time)
    if td is None: await interaction.followup.send("❌ Invalid duration.", ephemeral=True); return
    execute_time = discord.utils.utcnow() + td
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        tasks = await get_scheduled(session, guild_id)
        task_id = str(int(discord.utils.utcnow().timestamp() * 1000))
        tasks.append({"id": task_id, "action": action, "target": target, "reason": reason,
                      "execute_time": execute_time.isoformat(), "created_by": str(interaction.user.id),
                      "channel_id": str(interaction.channel_id)})
        await save_scheduled(session, guild_id, tasks)
    await interaction.followup.send(f"⏰ Scheduled **{action}** in **{format_timedelta(td)}** | Target: {target}", ephemeral=True)

@schedule_group.command(name="list", description="List pending scheduled tasks")
async def schedule_list(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.moderate_members: await interaction.response.send_message("❌ Mod only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        tasks = await get_scheduled(session, guild_id)
    now = discord.utils.utcnow()
    active = [t for t in tasks if datetime.datetime.fromisoformat(t['execute_time']) > now]
    e = discord.Embed(title="⏰ Scheduled Tasks", color=0x5865F2)
    if not active: e.description = "No scheduled tasks."
    else:
        for t in active[:10]:
            ts = int(datetime.datetime.fromisoformat(t['execute_time']).timestamp())
            e.add_field(name=f"{t['action'].upper()} — ID: {t['id'][-6:]}", value=f"Target: {t['target']}\n<t:{ts}:R>\n{t['reason'][:30]}", inline=False)
    await interaction.followup.send(embed=e, ephemeral=True)

@schedule_group.command(name="cancel", description="Cancel a scheduled task")
@app_commands.describe(task_id="Last 6 characters of task ID")
async def schedule_cancel(interaction: discord.Interaction, task_id: str):
    if not interaction.user.guild_permissions.moderate_members: await interaction.response.send_message("❌ Mod only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        tasks = await get_scheduled(session, guild_id)
        idx = next((i for i, t in enumerate(tasks) if t['id'].endswith(task_id)), None)
        if idx is None: await interaction.followup.send("❌ Task not found.", ephemeral=True); return
        del tasks[idx]
        await save_scheduled(session, guild_id, tasks)
    await interaction.followup.send("✅ Task cancelled.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# INVITES — fold invite_create/delete into /invites action
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# MISC COMMANDS THAT WERE REMOVED — restore as flat commands
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="autopublish", description="Enable/disable auto-publish in an announcement channel")
@app_commands.describe(channel="Announcement channel", enabled="Enable or disable")
async def autopublish_cmd(interaction: discord.Interaction, channel: discord.TextChannel, enabled: bool = True):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    if not channel.is_news(): await interaction.followup.send("❌ Must be an announcement channel.", ephemeral=True); return
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg.setdefault("autopublish", {})[str(channel.id)] = enabled
        await save_config(session, interaction.guild, cfg)
    await interaction.followup.send(f"✅ Auto-publish {'enabled' if enabled else 'disabled'} in {channel.mention}", ephemeral=True)

@bot.tree.command(name="verify_setup", description="Set up the verification system")
@app_commands.describe(role="Verified role", channel="Channel for verification panel")
async def verify_setup_cmd(interaction: discord.Interaction, role: discord.Role, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
        cfg["verified_role"] = str(role.id)
        await save_config(session, interaction.guild, cfg)
    e = discord.Embed(title="✅ Verification", description="Click the button below to verify yourself.", color=0x57F287)
    await channel.send(embed=e, view=VerificationView())
    await interaction.followup.send(f"✅ Verification panel posted in {channel.mention}", ephemeral=True)

@bot.tree.command(name="deadban", description="Mass ban new/unverified accounts")
@app_commands.describe(max_age_hours="Max account age in hours", require_avatar="Safe if they have avatar", reason="Ban reason")
async def deadban_cmd(interaction: discord.Interaction, max_age_hours: int = 24, require_avatar: bool = True, reason: str = "Dead ban"):
    if not interaction.user.guild_permissions.administrator: await interaction.response.send_message("❌ Admin only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    now = datetime.datetime.utcnow()
    banned = 0
    for member in interaction.guild.members:
        if member.bot or member.guild_permissions.administrator: continue
        age_h = (now - member.created_at.replace(tzinfo=None)).total_seconds() / 3600
        if age_h < max_age_hours:
            if require_avatar and member.avatar: continue
            try: await member.ban(reason=reason); banned += 1
            except: pass
    await interaction.followup.send(f"🔨 Dead ban complete: **{banned}** banned.", ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════════════


@tasks.loop(minutes=1)
async def check_temp_actions():
    """Check and execute scheduled temporary actions"""
    now = datetime.datetime.utcnow()
    async with aiohttp.ClientSession() as session:
        temp_data = await get_temp_actions(session)
        changed = False
        
        for guild_id, actions in list(temp_data.items()):
            for action in list(actions):
                if action.get("end_time"):
                    end_time = datetime.datetime.fromisoformat(action["end_time"])
                    if now >= end_time:
                        guild = bot.get_guild(int(guild_id))
                        if guild:
                            action_type = action.get("type")
                            
                            if action_type == "unban":
                                try:
                                    user = await bot.fetch_user(int(action["user_id"]))
                                    await guild.unban(user, reason="Tempban expired")
                                except:
                                    pass
                            
                            elif action_type == "unlock":
                                channel = guild.get_channel(int(action.get("channel_id", 0)))
                                if channel:
                                    try:
                                        overwrite = channel.overwrites_for(guild.default_role)
                                        overwrite.send_messages = None
                                        await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="Templock expired")
                                    except:
                                        pass
                        
                        actions.remove(action)
                        changed = True
        
        if changed:
            await save_temp_actions(session, temp_data)


@tasks.loop(minutes=1)
async def check_raidmode():
    """Auto-disable raid mode after 30 minutes of inactivity"""
    pass  # Could implement auto-disable logic here


# ══════════════════════════════════════════════════════════════════════════════
# REMINDER SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

FILE_REMINDERS = "reminders.json"
FILE_SCHEDULED = "scheduled.json"
FILE_CUSTOMCMDS = "customcmds.json"
FILE_INVITES = "invites.json"

async def get_reminders(session, guild_id: str = None) -> dict:
    all_r, _ = await gh_read(session, FILE_REMINDERS, guild_branch_from_id(guild_id))
    if not all_r:
        return {}
    if guild_id:
        return all_r.get(guild_id, {})
    return all_r

async def save_reminders(session, guild_id: str, reminders: dict):
    all_r, sha = await gh_read(session, FILE_REMINDERS, guild_branch_from_id(guild_id))
    if not all_r:
        all_r = {}
    all_r[guild_id] = reminders
    await gh_write(session, FILE_REMINDERS, all_r, sha, "Vortex: update reminders", guild_branch_from_id(guild_id))

async def get_scheduled(session, guild_id: str) -> list:
    all_s, _ = await gh_read(session, FILE_SCHEDULED, guild_branch_from_id(guild_id))
    if not all_s:
        return []
    return all_s.get(guild_id, [])

async def save_scheduled(session, guild_id: str, tasks: list):
    all_s, sha = await gh_read(session, FILE_SCHEDULED, guild_branch_from_id(guild_id))
    if not all_s:
        all_s = {}
    all_s[guild_id] = tasks
    await gh_write(session, FILE_SCHEDULED, all_s, sha, "Vortex: update scheduled", guild_branch_from_id(guild_id))

async def get_custom_cmds(session, guild_id: str) -> dict:
    all_c, _ = await gh_read(session, FILE_CUSTOMCMDS, guild_branch_from_id(guild_id))
    if not all_c:
        return {}
    return all_c.get(guild_id, {})

async def save_custom_cmds(session, guild_id: str, cmds: dict):
    all_c, sha = await gh_read(session, FILE_CUSTOMCMDS, guild_branch_from_id(guild_id))
    if not all_c:
        all_c = {}
    all_c[guild_id] = cmds
    await gh_write(session, FILE_CUSTOMCMDS, all_c, sha, "Vortex: update custom commands", guild_branch_from_id(guild_id))

async def get_invites(session, guild_id: str) -> dict:
    all_i, _ = await gh_read(session, FILE_INVITES, guild_branch_from_id(guild_id))
    if not all_i:
        return {}
    return all_i.get(guild_id, {})

async def save_invites(session, guild_id: str, invites: dict):
    all_i, sha = await gh_read(session, FILE_INVITES, guild_branch_from_id(guild_id))
    if not all_i:
        all_i = {}
    all_i[guild_id] = invites
    await gh_write(session, FILE_INVITES, all_i, sha, "Vortex: update invites", guild_branch_from_id(guild_id))


@bot.tree.command(name="remind", description="Set a reminder")
@app_commands.describe(time="Time until reminder (e.g., 1h, 30m, 1d)", message="Reminder message", user="User to remind (optional)")
async def remind_cmd(interaction: discord.Interaction, time: str, message: str, user: discord.Member = None):
    await interaction.response.defer(ephemeral=True)
    
    td = parse_duration(time)
    if td is None:
        await interaction.followup.send("❌ Invalid duration. Use format like 1h, 30m, 1d", ephemeral=True)
        return
    
    target_user = user or interaction.user
    remind_time = discord.utils.utcnow() + td
    
    guild_id = str(interaction.guild_id)
    async with aiohttp.ClientSession() as session:
        reminders = await get_reminders(session, guild_id)
        
        reminder_id = str(int(time.time() * 1000))
        reminders[reminder_id] = {
            "user_id": str(target_user.id),
            "channel_id": str(interaction.channel_id),
            "message": message,
            "remind_time": remind_time.isoformat(),
            "created_by": str(interaction.user.id),
        }
        await save_reminders(session, guild_id, reminders)
    
    await interaction.followup.send(f"⏰ Reminder set for {target_user.mention} in **{format_timedelta(td)}**\nMessage: {message}", ephemeral=True)


@bot.tree.command(name="reminders", description="List your reminders")
async def reminders_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        reminders = await get_reminders(session, guild_id)
    
    user_reminders = {k: v for k, v in reminders.items() if v.get("user_id") == str(interaction.user.id)}
    
    e = discord.Embed(title="⏰ Your Reminders", color=0x5865F2)
    
    if not user_reminders:
        e.description = "No active reminders."
    else:
        for rid, r in list(user_reminders.items())[:10]:
            remind_ts = int(datetime.datetime.fromisoformat(r['remind_time']).timestamp())
            e.add_field(name=f"ID: {rid[-6:]}", value=f"{r['message'][:50]}\n⏰ <t:{remind_ts}:R>", inline=False)
    
    await interaction.followup.send(embed=e, ephemeral=True)




@bot.event
async def on_invite_create(invite):
    guild_id = str(invite.guild.id)
    async with aiohttp.ClientSession() as session:
        invites = await get_invites(session, guild_id)
        invites[str(invite.code)] = {
            "code": invite.code,
            "inviter_id": str(invite.inviter.id) if invite.inviter else None,
            "uses": invite.uses,
            "created_at": datetime.datetime.utcnow().isoformat(),
        }
        await save_invites(session, guild_id, invites)


@bot.event
async def on_invite_delete(invite):
    guild_id = str(invite.guild.id)
    async with aiohttp.ClientSession() as session:
        invites = await get_invites(session, guild_id)
        invites.pop(str(invite.code), None)
        await save_invites(session, guild_id, invites)


@bot.tree.command(name="invites", description="Check invite count")
@app_commands.describe(member="Member to check (optional)")
async def invites_cmd(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        invites = await get_invites(session, guild_id)
    
    # Count invites by user
    user_invites = 0
    for code, data in invites.items():
        if data.get("inviter_id") == str(member.id):
            user_invites += 1
    
    e = discord.Embed(title=f"📨 Invites for {member.display_name}", color=0x5865F2)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="Total Invites", value=str(user_invites), inline=True)
    
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="invitelist", description="List all server invites")
@is_mod()
async def invitelist_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    invites = await interaction.guild.invites()
    
    e = discord.Embed(title=f"📨 Server Invites ({len(invites)})", color=0x5865F2)
    
    if not invites:
        e.description = "No invites."
    else:
        for inv in invites[:25]:
            inviter = inv.inviter.mention if inv.inviter else "Unknown"
            e.add_field(
                name=f"discord.gg/{inv.code}",
                value=f"By: {inviter}\nUses: {inv.uses}",
                inline=True
            )
    
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="embed", description="Send an embed")
@app_commands.describe(
    title="Embed title",
    description="Embed description",
    color="Color (hex, e.g., FF0000)",
    channel="Channel to send (optional)"
)
@is_mod()
async def embed_cmd(interaction: discord.Interaction, title: str, description: str = None, color: str = "5865F2", channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    
    try:
        color_int = int(color.replace("#", ""), 16)
    except:
        color_int = 0x5865F2
    
    e = discord.Embed(title=title, description=description, color=color_int)
    await channel.send(embed=e)
    await interaction.response.send_message(f"✅ Embed sent to {channel.mention}", ephemeral=True)


@bot.tree.command(name="say", description="Say something through the bot")
@app_commands.describe(message="Message to say", channel="Channel (optional)")
@is_mod()
async def say_cmd(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    await channel.send(message)
    await interaction.response.send_message(f"✅ Message sent to {channel.mention}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# UTILITY COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="random", description="Get a random number")
@app_commands.describe(min_val="Minimum value", max_val="Maximum value")
async def random_cmd(interaction: discord.Interaction, min_val: int = 1, max_val: int = 100):
    result = random.randint(min_val, max_val)
    await interaction.response.send_message(f"🎲 Random number: **{result}**")


@bot.tree.command(name="roll", description="Roll dice")
@app_commands.describe(dice="Dice notation (e.g., 2d6, 1d20)")
async def roll_cmd(interaction: discord.Interaction, dice: str = "1d6"):
    try:
        parts = dice.lower().split('d')
        if len(parts) != 2:
            raise ValueError()
        num = int(parts[0])
        sides = int(parts[1])
        if num < 1 or num > 100 or sides < 1 or sides > 1000:
            raise ValueError()
        
        rolls = [random.randint(1, sides) for _ in range(num)]
        total = sum(rolls)
        
        await interaction.response.send_message(f"🎲 Rolled {dice}: {rolls} = **{total}**")
    except:
        await interaction.response.send_message("❌ Invalid dice notation. Use format like `2d6` or `1d20`", ephemeral=True)


@bot.tree.command(name="coinflip", description="Flip a coin")
async def coinflip_cmd(interaction: discord.Interaction):
    result = random.choice(["Heads", "Tails"])
    await interaction.response.send_message(f"🪙 **{result}**!")


@bot.tree.command(name="8ball", description="Ask the magic 8-ball")
@app_commands.describe(question="Your question")
async def eightball_cmd(interaction: discord.Interaction, question: str):
    responses = [
        "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes definitely.",
        "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.",
        "Yes.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
        "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
        "Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."
    ]
    result = random.choice(responses)
    await interaction.response.send_message(f"🎱 **Question:** {question}\n**Answer:** {result}")


@bot.tree.command(name="choose", description="Choose from options")
@app_commands.describe(options="Options separated by ' or ' (e.g., pizza or pasta or sushi)")
async def choose_cmd(interaction: discord.Interaction, options: str):
    choices = [c.strip() for c in options.split(" or ") if c.strip()]
    if len(choices) < 2:
        await interaction.response.send_message("❌ Provide at least 2 options separated by ' or '", ephemeral=True)
        return
    result = random.choice(choices)
    await interaction.response.send_message(f"🤔 I choose: **{result}**")


@bot.tree.command(name="pick", description="Pick a random member")
async def pick_cmd(interaction: discord.Interaction):
    members = [m for m in interaction.guild.members if not m.bot]
    if not members:
        await interaction.response.send_message("❌ No members to pick from.", ephemeral=True)
        return
    result = random.choice(members)
    await interaction.response.send_message(f"👆 I pick: {result.mention}")


# ══════════════════════════════════════════════════════════════════════════════
# BOT INFO COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

_start_time = time.time()

@bot.tree.command(name="botinfo", description="View bot information")
async def botinfo_cmd(interaction: discord.Interaction):
    e = discord.Embed(title="🌀 Vortex", description="A powerful Discord moderation bot", color=0x5865F2)
    e.set_thumbnail(url=bot.user.display_avatar.url)
    
    e.add_field(name="Servers", value=len(bot.guilds), inline=True)
    e.add_field(name="Users", value=sum(g.member_count or 0 for g in bot.guilds), inline=True)
    e.add_field(name="Commands", value=len(bot.tree.get_commands()), inline=True)
    
    uptime = int(time.time() - _start_time)
    days, remainder = divmod(uptime, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
    e.add_field(name="Uptime", value=uptime_str, inline=True)
    
    e.add_field(name="Library", value="discord.py", inline=True)
    e.add_field(name="Version", value="2.0", inline=True)
    
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="ping", description="Check bot latency")
async def ping_cmd(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! **{latency}ms**")


@bot.tree.command(name="uptime", description="Check bot uptime")
async def uptime_cmd(interaction: discord.Interaction):
    uptime = int(time.time() - _start_time)
    days, remainder = divmod(uptime, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    await interaction.response.send_message(f"⏱️ Uptime: **{days}d {hours}h {minutes}m {seconds}s**")


@bot.tree.command(name="stats", description="View bot statistics")
async def stats_cmd(interaction: discord.Interaction):
    e = discord.Embed(title="📊 Bot Statistics", color=0x5865F2)
    
    e.add_field(name="Servers", value=len(bot.guilds), inline=True)
    e.add_field(name="Users", value=sum(g.member_count or 0 for g in bot.guilds), inline=True)
    e.add_field(name="Channels", value=sum(len(g.channels) for g in bot.guilds), inline=True)
    
    memory = f"{os.popen('ps -o rss= -p ' + str(os.getpid())).read().strip()} KB" if os.name != 'nt' else "N/A"
    e.add_field(name="Memory", value=memory, inline=True)
    
    latency = round(bot.latency * 1000)
    e.add_field(name="Latency", value=f"{latency}ms", inline=True)
    
    await interaction.response.send_message(embed=e)


# ══════════════════════════════════════════════════════════════════════════════
# ENHANCED VOICE COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="voice", description="Voice channel commands")
@app_commands.describe(
    action="Action to perform",
    member="Target member (for kick/move/mute/deafen)",
    channel="Target channel (for move)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="kick", value="kick"),
    app_commands.Choice(name="disconnect", value="disconnect"),
    app_commands.Choice(name="mute", value="mute"),
    app_commands.Choice(name="unmute", value="unmute"),
    app_commands.Choice(name="deafen", value="deafen"),
    app_commands.Choice(name="undeafen", value="undeafen"),
])
@is_mod()
async def voice_cmd(interaction: discord.Interaction, action: str, member: discord.Member = None, channel: discord.VoiceChannel = None):
    await interaction.response.defer(ephemeral=True)
    
    if action in ("kick", "disconnect"):
        if not member or not member.voice:
            await interaction.followup.send("❌ User not in voice.", ephemeral=True)
            return
        await member.move_to(None, reason=f"Voice {action}")
        await interaction.followup.send(f"✅ Disconnected **{member}** from voice.", ephemeral=True)
    
    elif action == "move":
        if not member or not member.voice or not channel:
            await interaction.followup.send("❌ Invalid parameters.", ephemeral=True)
            return
        await member.move_to(channel)
        await interaction.followup.send(f"✅ Moved **{member}** to {channel.mention}.", ephemeral=True)
    
    elif action == "mute":
        if not member or not member.voice:
            await interaction.followup.send("❌ User not in voice.", ephemeral=True)
            return
        await member.edit(mute=True)
        await interaction.followup.send(f"✅ Server muted **{member}**.", ephemeral=True)
    
    elif action == "unmute":
        if not member:
            await interaction.followup.send("❌ No member specified.", ephemeral=True)
            return
        await member.edit(mute=False)
        await interaction.followup.send(f"✅ Unmuted **{member}**.", ephemeral=True)
    
    elif action == "deafen":
        if not member or not member.voice:
            await interaction.followup.send("❌ User not in voice.", ephemeral=True)
            return
        await member.edit(deafen=True)
        await interaction.followup.send(f"✅ Server deafened **{member}**.", ephemeral=True)
    
    elif action == "undeafen":
        if not member:
            await interaction.followup.send("❌ No member specified.", ephemeral=True)
            return
        await member.edit(deafen=False)
        await interaction.followup.send(f"✅ Undeafened **{member}**.", ephemeral=True)


@bot.tree.command(name="perms", description="Check user permissions")
@app_commands.describe(member="Member to check")
async def perms_cmd(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    perms = member.guild_permissions
    
    e = discord.Embed(title=f"🔐 Permissions for {member.display_name}", color=member.color)
    
    perm_list = []
    for name, value in perms:
        if value:
            perm_list.append(f"✅ {name.replace('_', ' ').title()}")
    
    e.description = "\n".join(perm_list[:25]) or "No special permissions"
    
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="search", description="Search cases")
@app_commands.describe(
    user="Filter by user",
    mod="Filter by moderator",
    action_type="Filter by action type"
)
@is_mod()
async def search_cmd(interaction: discord.Interaction, user: discord.Member = None, mod: discord.Member = None, action_type: str = None):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        all_cases = await get_cases(session, interaction.guild)
    
    results = all_cases
    
    if user:
        results = [c for c in results if c.get("target_id") == str(user.id)]
    if mod:
        results = [c for c in results if c.get("mod_id") == str(mod.id)]
    if action_type:
        results = [c for c in results if c.get("action", "").lower() == action_type.lower()]
    
    e = discord.Embed(title=f"🔍 Search Results ({len(results)} found)", color=0x5865F2)
    
    if not results:
        e.description = "No matching cases found."
    else:
        for c in reversed(results[-10:]):
            ts = int(datetime.datetime.fromisoformat(c['timestamp']).timestamp())
            e.add_field(
                name=f"#{c['id']} - {c['action'].upper()}",
                value=f"Target: {c['target_name']}\nMod: {c['mod_name']}\n<t:{ts}:R>",
                inline=False
            )
    
    await interaction.followup.send(embed=e, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# TEMPORARY ACTIONS BACKGROUND TASK
# ══════════════════════════════════════════════════════════════════════════════

@tasks.loop(minutes=1)
async def check_reminders():
    """Check and send due reminders"""
    now = discord.utils.utcnow()
    async with aiohttp.ClientSession() as session:
        all_reminders = await get_reminders(session)
        
        for guild_id, reminders in all_reminders.items():
            changed = False
            for rid, r in list(reminders.items()):
                remind_time = datetime.datetime.fromisoformat(r['remind_time'])
                if now >= remind_time:
                    guild = bot.get_guild(int(guild_id))
                    if guild:
                        channel = guild.get_channel(int(r['channel_id']))
                        user = guild.get_member(int(r['user_id']))
                        if channel and user:
                            try:
                                await channel.send(f"⏰ {user.mention} Reminder: {r['message']}")
                            except:
                                pass
                    del reminders[rid]
                    changed = True
            
            if changed:
                await save_reminders(session, guild_id, reminders)


@tasks.loop(minutes=1)
async def check_scheduled_tasks():
    """Check and execute scheduled tasks"""
    now = discord.utils.utcnow()
    async with aiohttp.ClientSession() as session:
        all_tasks = await get_scheduled(session, "")
        
        for guild_id in list(all_tasks.keys()):
            tasks = all_tasks.get(guild_id, [])
            changed = False
            
            for t in list(tasks):
                exec_time = datetime.datetime.fromisoformat(t['execute_time'])
                if now >= exec_time:
                    guild = bot.get_guild(int(guild_id))
                    if guild:
                        try:
                            action = t['action']
                            target = t['target']
                            reason = t['reason']
                            
                            if action == "ban":
                                user = await bot.fetch_user(int(target))
                                await guild.ban(user, reason=reason)
                            elif action == "unban":
                                user = await bot.fetch_user(int(target))
                                await guild.unban(user, reason=reason)
                            elif action == "mute":
                                member = guild.get_member(int(target))
                                if member:
                                    until = discord.utils.utcnow() + datetime.timedelta(hours=1)
                                    await member.timeout(until, reason=reason)
                            elif action == "unmute":
                                member = guild.get_member(int(target))
                                if member:
                                    await member.timeout(None, reason=reason)
                            elif action == "kick":
                                member = guild.get_member(int(target))
                                if member:
                                    await member.kick(reason=reason)
                            elif action == "lock":
                                channel = guild.get_channel(int(target))
                                if channel:
                                    overwrite = channel.overwrites_for(guild.default_role)
                                    overwrite.send_messages = False
                                    await channel.set_permissions(guild.default_role, overwrite=overwrite)
                            elif action == "unlock":
                                channel = guild.get_channel(int(target))
                                if channel:
                                    overwrite = channel.overwrites_for(guild.default_role)
                                    overwrite.send_messages = None
                                    await channel.set_permissions(guild.default_role, overwrite=overwrite)
                        except Exception:
                            pass
                    
                    tasks.remove(t)
                    changed = True
            
            if changed:
                await save_scheduled(session, guild_id, tasks)


# ══════════════════════════════════════════════════════════════════════════════
# AUTOROLE SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

FILE_AUTOROLE = "autorole.json"
FILE_STARBOARD = "starboard.json"
FILE_STICKYROLES = "stickyroles.json"
FILE_BIRTHDAYS = "birthdays.json"

async def get_autorole(session, guild_id: str) -> dict:
    all_a, _ = await gh_read(session, FILE_AUTOROLE, guild_branch_from_id(guild_id))
    if not all_a:
        return {}
    return all_a.get(guild_id, {})

async def save_autorole(session, guild_id: str, data: dict):
    all_a, sha = await gh_read(session, FILE_AUTOROLE, guild_branch_from_id(guild_id))
    if not all_a:
        all_a = {}
    all_a[guild_id] = data
    await gh_write(session, FILE_AUTOROLE, all_a, sha, "Vortex: update autorole", guild_branch_from_id(guild_id))

async def get_sticky_roles(session, guild_id: str) -> dict:
    all_s, _ = await gh_read(session, FILE_STICKYROLES, guild_branch_from_id(guild_id))
    if not all_s:
        return {}
    return all_s.get(guild_id, {})

async def save_sticky_roles(session, guild_id: str, data: dict):
    all_s, sha = await gh_read(session, FILE_STICKYROLES, guild_branch_from_id(guild_id))
    if not all_s:
        all_s = {}
    all_s[guild_id] = data
    await gh_write(session, FILE_STICKYROLES, all_s, sha, "Vortex: update sticky roles", guild_branch_from_id(guild_id))

async def get_starboard(session, guild_id: str) -> dict:
    all_s, _ = await gh_read(session, FILE_STARBOARD, guild_branch_from_id(guild_id))
    if not all_s:
        return {}
    return all_s.get(guild_id, {})

async def save_starboard(session, guild_id: str, data: dict):
    all_s, sha = await gh_read(session, FILE_STARBOARD, guild_branch_from_id(guild_id))
    if not all_s:
        all_s = {}
    all_s[guild_id] = data
    await gh_write(session, FILE_STARBOARD, all_s, sha, "Vortex: update starboard", guild_branch_from_id(guild_id))

async def get_birthdays(session, guild_id: str) -> dict:
    all_b, _ = await gh_read(session, FILE_BIRTHDAYS, guild_branch_from_id(guild_id))
    if not all_b:
        return {}
    return all_b.get(guild_id, {})

async def save_birthdays(session, guild_id: str, data: dict):
    all_b, sha = await gh_read(session, FILE_BIRTHDAYS, guild_branch_from_id(guild_id))
    if not all_b:
        all_b = {}
    all_b[guild_id] = data
    await gh_write(session, FILE_BIRTHDAYS, all_b, sha, "Vortex: update birthdays", guild_branch_from_id(guild_id))


@bot.tree.command(name="autorole", description="Configure auto-role on join")
@app_commands.describe(role="Role to auto-assign", enabled="Enable or disable")
@is_admin()
async def autorole_cmd(interaction: discord.Interaction, role: discord.Role, enabled: bool = True):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        ar = await get_autorole(session, guild_id)
        ar["role_id"] = str(role.id) if enabled else None
        ar["enabled"] = enabled
        await save_autorole(session, guild_id, ar)
    
    status = "enabled" if enabled else "disabled"
    await interaction.followup.send(f"✅ Autorole {status}: {role.mention}", ephemeral=True)


@bot.tree.command(name="vote", description="Quick yes/no vote")
@app_commands.describe(question="Question to vote on")
async def vote_cmd(interaction: discord.Interaction, question: str):
    e = discord.Embed(title=f"📊 Vote: {question}", color=0x5865F2)
    e.description = "React below to vote!"
    e.set_footer(text=f"Vote by {interaction.user.display_name}")
    
    msg = await interaction.response.send_message(embed=e)
    msg = await interaction.original_response()
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")


# ══════════════════════════════════════════════════════════════════════════════
# VERIFICATION SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

class VerificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="vortex:verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild_id)
        
        async with aiohttp.ClientSession() as session:
            cfg = await get_config(session, interaction.guild)
        
        verified_role_id = cfg.get("verified_role")
        if not verified_role_id:
            await interaction.response.send_message("❌ Verification not set up.", ephemeral=True)
            return
        
        role = interaction.guild.get_role(int(verified_role_id))
        if not role:
            await interaction.response.send_message("❌ Verified role not found.", ephemeral=True)
            return
        
        if role in interaction.user.roles:
            await interaction.response.send_message("✅ You're already verified!", ephemeral=True)
            return
        
        await interaction.user.add_roles(role, reason="Verification")
        await interaction.response.send_message(f"✅ You have been verified! {role.mention}", ephemeral=True)


@bot.tree.command(name="unverify", description="Remove verification from user")
@app_commands.describe(member="Member to unverify")
@is_mod()
async def unverify_cmd(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    guild_id = str(interaction.guild_id)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, interaction.guild)
    
    verified_role_id = cfg.get("verified_role")
    if verified_role_id:
        role = interaction.guild.get_role(int(verified_role_id))
        if role and role in member.roles:
            await member.remove_roles(role, reason="Unverified by mod")
            await interaction.followup.send(f"✅ Removed verification from {member.mention}", ephemeral=True)
            return
    
    await interaction.followup.send("❌ User is not verified.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# UPDATE EVENTS FOR AUTOROLE, STICKY ROLES, ETC
# ══════════════════════════════════════════════════════════════════════════════

# Update on_member_join to include autorole
_original_on_member_join = on_member_join.callback

@bot.event
async def on_member_join(member: discord.Member):
    # Call original handler
    await _original_on_member_join(member)
    
    guild_id = str(member.guild.id)
    
    async with aiohttp.ClientSession() as session:
        # Autorole
        ar = await get_autorole(session, guild_id)
        if ar.get("enabled") and ar.get("role_id"):
            role = member.guild.get_role(int(ar["role_id"]))
            if role:
                delay = ar.get("delay", 0)
                if delay > 0:
                    await asyncio.sleep(delay)
                try:
                    await member.add_roles(role, reason="Autorole")
                except:
                    pass
        
        # Bot autorole
        if member.bot and ar.get("bot_role_id"):
            bot_role = member.guild.get_role(int(ar["bot_role_id"]))
            if bot_role:
                try:
                    await member.add_roles(bot_role, reason="Bot autorole")
                except:
                    pass
        
        # Sticky roles - restore roles on rejoin
        cfg = await get_config(session, member.guild)
        if cfg.get("sticky_roles"):
            sticky = await get_sticky_roles(session, guild_id)
            if str(member.id) in sticky:
                for role_id in sticky[str(member.id)]:
                    role = member.guild.get_role(int(role_id))
                    if role:
                        try:
                            await member.add_roles(role, reason="Sticky role restore")
                        except:
                            pass


# Save roles on member leave for sticky roles
@bot.event
async def on_member_remove(member: discord.Member):
    guild_id = str(member.guild.id)
    
    async with aiohttp.ClientSession() as session:
        cfg = await get_config(session, member.guild)
        
        if cfg.get("sticky_roles"):
            sticky = await get_sticky_roles(session, guild_id)
            # Save non-default roles
            sticky[str(member.id)] = [str(r.id) for r in member.roles if r.name != "@everyone" and not r.managed]
            await save_sticky_roles(session, guild_id, sticky)
        
        # Original leave logging
        if cfg.get("logging", {}).get("member_leave") and cfg.get("mod_log"):
            e = discord.Embed(title="🔴 Member left", color=0xED4245, timestamp=datetime.datetime.utcnow())
            e.set_thumbnail(url=member.display_avatar.url)
            e.add_field(name="User", value=f"{member} ({member.id})", inline=False)
            await send_mod_log(member.guild, cfg, e)


# ══════════════════════════════════════════════════════════════════════════════
# STARBOARD HANDLER
# ══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    # Original reaction role handler
    await _original_reaction_add(payload)
    
    if not payload.guild_id:
        return
    
    guild_id = str(payload.guild_id)
    
    async with aiohttp.ClientSession() as session:
        sb = await get_starboard(session, guild_id)
    
    if not sb.get("enabled"):
        return
    
    channel = bot.get_channel(payload.channel_id)
    if not channel:
        return
    
    try:
        msg = await channel.fetch_message(payload.message_id)
    except:
        return
    
    # Check if emoji matches
    star_emoji = sb.get("emoji", "⭐")
    if str(payload.emoji) != star_emoji:
        return
    
    # Count stars
    for reaction in msg.reactions:
        if str(reaction.emoji) == star_emoji:
            count = reaction.count
            break
    else:
        return
    
    threshold = sb.get("threshold", 3)
    if count < threshold:
        return
    
    # Check if already starred
    starred = sb.get("starred", {})
    if str(payload.message_id) in starred:
        # Update existing starboard message
        star_ch = bot.get_channel(int(sb["channel_id"]))
        if star_ch:
            try:
                star_msg = await star_ch.fetch_message(int(starred[str(payload.message_id)]))
                e = discord.Embed(
                    title=f"⭐ {count} | {msg.channel.name}",
                    description=msg.content[:1500],
                    color=0xFFD700,
                    timestamp=msg.created_at,
                )
                e.set_author(name=str(msg.author), icon_url=msg.author.display_avatar.url)
                e.add_field(name="Source", value=f"[Jump]({msg.jump_url})", inline=False)
                await star_msg.edit(embed=e)
            except:
                pass
        return
    
    # Create new starboard entry
    star_ch = bot.get_channel(int(sb["channel_id"]))
    if not star_ch:
        return
    
    e = discord.Embed(
        title=f"⭐ {count} | {msg.channel.name}",
        description=msg.content[:1500],
        color=0xFFD700,
        timestamp=msg.created_at,
    )
    e.set_author(name=str(msg.author), icon_url=msg.author.display_avatar.url)
    if msg.attachments:
        e.set_image(url=msg.attachments[0].url)
    e.add_field(name="Source", value=f"[Jump]({msg.jump_url})", inline=False)
    
    star_msg = await star_ch.send(embed=e)
    
    # Save to starred
    if "starred" not in sb:
        sb["starred"] = {}
    sb["starred"][str(payload.message_id)] = str(star_msg.id)
    
    async with aiohttp.ClientSession() as session:
        await save_starboard(session, guild_id, sb)


# Store original reaction handler
_original_reaction_add = on_raw_reaction_add.callback


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-PUBLISH HANDLER
# ══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_message(message: discord.Message):
    # Check for auto-publish
    if message.guild and not message.author.bot:
        guild_id = str(message.guild.id)
        
        async with aiohttp.ClientSession() as session:
            cfg = await get_config(session, message.guild)
        
        autopublish = cfg.get("autopublish", {})
        if autopublish.get(str(message.channel.id)) and message.channel.is_news():
            try:
                await message.publish()
            except:
                pass
    
    # Call original on_message
    await bot.process_commands(message)


# ══════════════════════════════════════════════════════════════════════════════
# LEVEL ROLE REWARDS CHECK
# ══════════════════════════════════════════════════════════════════════════════

# Update the leveling system to include role rewards
async def check_level_roles(member: discord.Member, level: int, session):
    guild_id = str(member.guild.id)
    cfg = await get_config(session, member.guild)
    level_roles = cfg.get("level_roles", {})
    
    # Check all level thresholds up to current level
    for lvl_str, role_id in level_roles.items():
        if int(lvl_str) <= level:
            role = member.guild.get_role(int(role_id))
            if role and role not in member.roles:
                await member.add_roles(role, reason=f"Level {lvl_str} reward")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    await start_health_server()
    
    # Start all background tasks
    if not check_giveaways.is_running():
        check_giveaways.start()
    if not check_temp_actions.is_running():
        check_temp_actions.start()
    if not check_raidmode.is_running():
        check_raidmode.start()
    if not check_reminders.is_running():
        check_reminders.start()
    if not check_scheduled_tasks.is_running():
        check_scheduled_tasks.start()
    
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
