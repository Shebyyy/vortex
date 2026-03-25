# Vortex

> A powerful, feature-rich Discord moderation bot built with discord.py

[![discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)](https://github.com/Rapptz/discord.py)
[![Python](https://img.shields.io/badge/python-3.10+-green.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/discord-server-5865F2.svg)](https://discord.gg/)

---

## Table of Contents

1. [Features](#features)
2. [Setup](#setup)
3. [Commands](#commands)
4. [Configuration](#configuration)
5. [Self-Hosting](#self-hosting)
6. [License](#license)

---

## Features

<details>
<summary><strong>Click to expand features list</strong></summary>

### Moderation

- **Ban System** - Ban, hackban, forceban, softban, tempban, massban
- **Kick System** - Kick, masskick
- **Mute System** - Mute, tempmute, unmute (timeout support)
- **Lockdown** - Lock/unlock channels, templock, server-wide lockdown
- **Slowmode** - Set channel slowmode with duration parsing
- **Voice Moderation** - Voice kick, voice mute, move all, disconnect all
- **Role Management** - Add/remove roles, create/delete roles, role info
- **Nickname Control** - Change nickname, reset nickname, dehoist
- **Purge** - Delete messages with filters (user, contains, bot, emoji)

### Security

- **Honeypot System** - Catch compromised/hacked accounts automatically
- **Raid Protection** - Raid mode, panic mode, account age filter
- **Auto-Moderation** - Spam, caps, links, invites, mentions, zalgo, emojis, newlines

### Management

- **Warning System** - Points-based warnings with auto-punishment
- **Case System** - Track all moderation actions with case IDs
- **Logging** - Comprehensive audit logging for all events
- **Tickets** - Support ticket system with transcripts
- **Giveaways** - Timed giveaways with auto-roll
- **Leveling** - XP and leveling system

### Information

- **User Info** - Badges, account age, join age, voice status, mutual servers
- **Server Info** - Owner, features, boost level, channel counts
- **Role Info** - Permissions, members, creation date
- **Avatar/Banner** - View user avatar and banner images

</details>

---

## Setup

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | Required for modern discord.py features |
| Discord Bot Token | - | Create at [Discord Developer Portal](https://discord.com/developers/applications) |
| GitHub Token | - | For data storage (optional but recommended) |

### Installation

**Step 1:** Clone the repository

```bash
git clone https://github.com/Shebyyy/vortex.git
cd vortex
```

**Step 2:** Create virtual environment (recommended)

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows
```

**Step 3:** Install dependencies

```bash
pip install -r requirements.txt
```

**Step 4:** Set environment variables

```bash
# Linux/macOS
export DISCORD_TOKEN=your_bot_token
export GITHUB_TOKEN=your_github_token

# Windows
set DISCORD_TOKEN=your_bot_token
set GITHUB_TOKEN=your_github_token
```

**Step 5:** Run the bot

```bash
python bot.py
```

---

## Commands

### Ban Commands

<details>
<summary><strong>View all ban commands</strong></summary>

| Command | Description | Usage |
|---------|-------------|-------|
| `/ban` | Ban a member from the server | `/ban @user [reason] [delete_days: 0-7]` |
| `/hackban` | Ban user by ID even if not in server | `/hackban user_id [reason]` |
| `/forceban` | Alias for hackban | `/forceban user_id [reason]` |
| `/softban` | Ban and unban to delete messages | `/softban @user [reason]` |
| `/tempban` | Temporarily ban a member | `/tempban @user duration [reason]` |
| `/massban` | Ban multiple users at once | `/massban user_id1 user_id2 [reason]` |
| `/unban` | Unban a user by ID | `/unban user_id [reason]` |
| `/banlist` | List all banned users | `/banlist` |
| `/checkban` | Check if a user is banned | `/checkban user_id` |

**Duration Examples for Tempban:**

```text
1s, 30s     - Seconds
5m, 30m     - Minutes
1h, 24h     - Hours
1d, 7d      - Days
1w, 2w      - Weeks
```

</details>

---

### Kick Commands

<details>
<summary><strong>View all kick commands</strong></summary>

| Command | Description | Usage |
|---------|-------------|-------|
| `/kick` | Kick a member from the server | `/kick @user [reason]` |
| `/masskick` | Kick multiple users at once | `/masskick @user1 @user2 [reason]` |

**Notes:**

- Kick removes the user from the server but they can rejoin
- Masskick supports up to 10 users at once
- Requires `Kick Members` permission

</details>

---

### Mute Commands

<details>
<summary><strong>View all mute commands</strong></summary>

| Command | Description | Usage |
|---------|-------------|-------|
| `/mute` | Timeout a member | `/mute @user duration [reason]` |
| `/tempmute` | Temporarily mute a member | `/tempmute @user duration [reason]` |
| `/unmute` | Remove timeout from member | `/unmute @user` |

**Technical Details:**

> Uses Discord's native timeout feature (up to 28 days)
> Requires `Moderate Members` permission

</details>

---

### Lockdown Commands

<details>
<summary><strong>View all lockdown commands</strong></summary>

| Command | Description | Usage |
|---------|-------------|-------|
| `/lock` | Lock a channel | `/lock [channel] [reason]` |
| `/unlock` | Unlock a channel | `/unlock [channel]` |
| `/templock` | Temporarily lock a channel | `/templock duration [channel]` |
| `/lockdown` | Lock all channels | `/lockdown [reason]` |
| `/unlockdown` | Unlock all channels | `/unlockdown` |
| `/slowmode` | Set channel slowmode | `/slowmode seconds [channel]` |

**What happens during lock:**

```text
1. @everyone loses Send Messages permission
2. Bot stores original permissions
3. Unlock restores original permissions
```

</details>

---

### Voice Moderation Commands

<details>
<summary><strong>View all voice commands</strong></summary>

| Command | Description | Usage |
|---------|-------------|-------|
| `/voicekick` | Kick user from voice channel | `/voicekick @user [reason]` |
| `/voicemute` | Server mute user in voice | `/voicemute @user [reason]` |
| `/voiceunmute` | Remove server mute | `/voiceunmute @user` |
| `/moveall` | Move all users to channel | `/moveall #target_channel` |
| `/disconnectall` | Disconnect all from voice | `/disconnectall [channel]` |

</details>

---

### Role Management Commands

<details>
<summary><strong>View all role commands</strong></summary>

| Command | Description | Usage |
|---------|-------------|-------|
| `/role add` | Add role to user | `/role add @user @role` |
| `/role remove` | Remove role from user | `/role remove @user @role` |
| `/role create` | Create a new role | `/role create name [color]` |
| `/role delete` | Delete a role | `/role delete @role` |
| `/roleinfo` | View role information | `/roleinfo @role` |

**Roleinfo displays:**

```text
- Role name and ID
- Color (hex)
- Position
- Member count
- Creation date
- Permissions list
- Is mentionable
- Is hoisted
```

</details>

---

### Nickname Commands

<details>
<summary><strong>View all nickname commands</strong></summary>

| Command | Description | Usage |
|---------|-------------|-------|
| `/nickname` | Change user's nickname | `/nickname @user new_nickname` |
| `/nickname reset` | Reset user's nickname | `/nickname reset @user` |
| `/dehoist` | Remove hoisting characters from all | `/dehoist` |

**Hoisting Characters:**

> `! " # $ % & ' ( ) * + , - . / : ; < = > ? @ [ \ ] ^ _ \` { | } ~`
>
> Users with these at the start of their name appear at the top of the member list

</details>

---

### Purge Commands

<details>
<summary><strong>View all purge commands</strong></summary>

| Command | Description | Usage |
|---------|-------------|-------|
| `/purge` | Delete messages | `/purge amount` |
| `/purge user` | Delete messages from user | `/purge user @user amount` |
| `/purge contains` | Delete messages containing text | `/purge contains text amount` |
| `/purge bot` | Delete bot messages | `/purge bot amount` |
| `/purge emoji` | Delete messages with emojis | `/purge emoji amount` |

**Limits:**

```text
- Maximum 1000 messages per purge
- Messages must be under 14 days old (Discord API limit)
- Requires Manage Messages permission
```

</details>

---

### Information Commands

<details>
<summary><strong>View all info commands</strong></summary>

#### User Information

| Command | Description | Usage |
|---------|-------------|-------|
| `/userinfo` | View detailed user info | `/userinfo [@user]` |
| `/avatar` | View user avatar | `/avatar [@user]` |
| `/banner` | View user banner | `/banner [@user]` |
| `/lookup` | Lookup user by ID | `/lookup user_id` |

**Userinfo displays:**

```text
Username / Display Name
User ID
Badges (HypeSquad, Nitro, Booster, etc.)
Account Created
Server Joined
Join Position
Roles (with count)
Voice Status (channel, muted, deafened)
Permissions (key permissions)
Mutual Servers
Nitro Status
Banner
```

#### Server Information

| Command | Description | Usage |
|---------|-------------|-------|
| `/serverinfo` | View server information | `/serverinfo` |

**Serverinfo displays:**

```text
Server Name and ID
Owner
Created Date
Member Count / Max Members
Boost Level / Boost Count
Verification Level
Features (Community, Discovery, etc.)
Text Channels / Voice Channels
Categories
Roles Count
Emojis Count
Stickers Count
Server Icon / Banner
```

#### Role Information

| Command | Description | Usage |
|---------|-------------|-------|
| `/roleinfo` | View role information | `/roleinfo @role` |

</details>

---

### Auto-Moderation

<details>
<summary><strong>View all automod commands</strong></summary>

| Command | Description |
|---------|-------------|
| `/automod enable` | Enable auto-moderation |
| `/automod disable` | Disable auto-moderation |
| `/automod settings` | View current settings |
| `/automod spam` | Configure spam detection |
| `/automod caps` | Configure caps filter |
| `/automod links` | Configure link filter |
| `/automod invites` | Configure invite filter |
| `/automod mentions` | Configure mention spam |
| `/automod words` | Configure word blacklist |
| `/automod emojis` | Configure emoji spam filter |
| `/automod newlines` | Configure newline spam filter |
| `/automod zalgo` | Configure zalgo text filter |

**Available Filters:**

| Filter | Description | Configurable Options |
|--------|-------------|---------------------|
| **Spam** | Detect rapid message sending | `max_messages`, `interval` |
| **Caps** | Detect excessive capitalization | `threshold` (percentage) |
| **Links** | Block or filter URLs | `whitelist` |
| **Invites** | Block Discord invite links | - |
| **Mentions** | Detect mass mentions | `max` mentions |
| **Words** | Block specific words | `blacklist` |
| **Emojis** | Detect emoji spam | `max` emojis |
| **Newlines** | Detect excessive newlines | `max` newlines |
| **Zalgo** | Detect zalgo/glitch text | - |

**Actions taken on violation:**

1. Delete message
2. Warn user in channel
3. Mute user (for severe violations)
4. Log to mod-log channel

</details>

---

### Honeypot System

<details>
<summary><strong>View honeypot documentation</strong></summary>

The honeypot system catches compromised/hacked accounts and malicious bots automatically.

#### Commands

| Command | Description |
|---------|-------------|
| `/honeypot_add` | Mark channel as honeypot trap |
| `/honeypot_remove` | Remove honeypot from channel |
| `/honeypot_list` | List all honeypot channels |
| `/honeypot_protect` | Add role to protection list |
| `/honeypot_unprotect` | Remove role from protection list |

#### How It Works

```text
Step 1: Admin sets a channel as honeypot
        /honeypot_add #channel

Step 2: Bot sends warning message in channel

Step 3: Anyone who messages in that channel:
        - Gets kicked from server
        - All messages from last 24h deleted
        - Logged to mod-log

Step 4: Protected roles (mods/admins/custom) are exempt
```

#### Warning Message

Default message sent when honeypot is activated:

```text
Bot Trap Active

ATTENTION: This is a bot trap

DO NOT post anything here. You will be banned.
This channel is for catching compromised accounts and malicious bots only.
```

#### Custom Warning Message

```bash
/honeypot_add #channel warning_message:"Your custom warning here"
```

#### Protected Roles

Users with these are automatically protected:

- Administrator permission
- Moderate Members permission
- Custom roles added via `/honeypot_protect @role`

#### Ban Reason

```text
Honeypot: Compromised/hacked account detected
```

This reason is used because honeypot triggers typically indicate:

- Account token theft
- Self-bot usage
- Malicious bot scripts
- Compromised credentials

</details>

---

### Raid Protection

<details>
<summary><strong>View raid protection documentation</strong></summary>

#### Commands

| Command | Description | Usage |
|---------|-------------|-------|
| `/raidmode on` | Enable raid mode | `/raidmode on` |
| `/raidmode off` | Disable raid mode | `/raidmode off` |
| `/panic` | Enable panic mode (lockdown + raidmode) | `/panic` |
| `/raidstats` | View raid statistics | `/raidstats` |
| `/setaccountage` | Set minimum account age filter | `/setaccountage hours` |

#### Raid Mode

When enabled:

1. All new members are auto-kicked
2. Reason: "Raid mode active"
3. Logged to mod-log channel

#### Panic Mode

Activates all emergency measures:

1. Raid mode enabled
2. All channels locked
3. Only mods/admins can send messages
4. Logged to mod-log

#### Account Age Filter

Automatically kicks new accounts under specified age:

```bash
/setaccountage 24  # Requires 24 hour old accounts
/setaccountage 72  # Requires 3 day old accounts
/setaccountage 168 # Requires 1 week old accounts
```

</details>

---

### Warning System

<details>
<summary><strong>View warning system documentation</strong></summary>

#### Commands

| Command | Description | Usage |
|---------|-------------|-------|
| `/warn` | Warn a user | `/warn @user [reason]` |
| `/warnings` | View user warnings | `/warnings [@user]` |
| `/delwarn` | Delete a warning | `/delwarn @user warn_id` |
| `/editwarn` | Edit warning reason | `/editwarn @user warn_id new_reason` |
| `/clearwarns` | Clear all user warnings | `/clearwarns @user` |

#### Auto-Punishment System

Default thresholds:

| Warnings | Action |
|----------|--------|
| 3 warnings | Auto mute (1 hour) |
| 5 warnings | Auto kick |
| 7 warnings | Auto ban |

> Thresholds are configurable via server configuration

#### Warning Storage

```json
{
  "user_id": {
    "warnings": [
      {
        "id": 1,
        "reason": "Spamming in general",
        "mod_id": "123456789",
        "timestamp": "2024-01-01T00:00:00Z"
      }
    ]
  }
}
```

</details>

---

### Case System

<details>
<summary><strong>View case system documentation</strong></summary>

#### Commands

| Command | Description | Usage |
|---------|-------------|-------|
| `/case` | View specific case | `/case case_id` |
| `/cases` | View all cases | `/cases [@user]` |
| `/delcase` | Delete a case | `/delcase case_id` |
| `/editcase` | Edit case reason | `/editcase case_id new_reason` |

#### Case Actions Tracked

- Ban / Unban
- Kick
- Mute / Unmute
- Warn
- Softban
- Tempban
- Quarantine
- Lockdown
- Raid mode activation

#### Case Data Structure

```json
{
  "id": 1,
  "action": "ban",
  "mod_id": "123456789",
  "mod_name": "Moderator#0001",
  "target_id": "987654321",
  "target_name": "User#0001",
  "reason": "Spamming",
  "timestamp": "2024-01-01T00:00:00Z",
  "active": true
}
```

</details>

---

### Utility Commands

<details>
<summary><strong>View utility commands</strong></summary>

| Command | Description | Usage |
|---------|-------------|-------|
| `/poll` | Create a poll | `/poll question option1 option2 ...` |
| `/remind` | Set a reminder | `/remind duration message` |
| `/snipe` | View last deleted message | `/snipe` |
| `/ghostping` | View ghost pings | `/ghostping` |
| `/clean` | Clean bot messages | `/clean amount` |

#### Poll Example

```bash
/poll "Best programming language?" Python JavaScript Rust
```

Creates a poll with reactions for voting.

#### Reminder Example

```bash
/remind 1h Check the server
/remind 1d Meeting with staff
```

</details>

---

### Setup Commands

<details>
<summary><strong>View setup commands</strong></summary>

| Command | Description | Usage |
|---------|-------------|-------|
| `/setup modlog` | Set mod log channel | `/setup modlog #channel` |
| `/setup welcome` | Set welcome channel | `/setup welcome #channel` |
| `/setup muted` | Set muted role | `/setup muted @role` |
| `/setup adminrole` | Set admin role | `/setup adminrole @role` |
| `/setup modrole` | Set mod role | `/setup modrole @role` |
| `/setup reset` | Reset server config | `/setup reset` |

#### Welcome Message Variables

```text
{user}    - Mentions the new user
{server}  - Server name
```

Example:

```text
Welcome {user} to {server}! Read the rules and have fun!
```

Becomes:

```text
Welcome @NewUser to My Awesome Server! Read the rules and have fun!
```

</details>

---

## Configuration

<details>
<summary><strong>View configuration options</strong></summary>

### Default Configuration

```json
{
  "mod_log": null,
  "welcome_channel": null,
  "welcome_message": "Welcome {user} to {server}!",
  "muted_role": null,
  "quarantine_role": null,
  "verified_role": null,
  "ticket_category": null,
  "ticket_log": null,
  "mod_roles": [],
  "admin_roles": [],
  "honeypot_protected_roles": [],
  "automod": {
    "spam": {"enabled": false, "max_messages": 5, "interval": 5},
    "caps": {"enabled": false, "threshold": 70},
    "links": {"enabled": false, "whitelist": []},
    "words": {"enabled": false, "blacklist": []},
    "invites": {"enabled": false},
    "mentions": {"enabled": false, "max": 5},
    "emojis": {"enabled": false, "max": 10},
    "newlines": {"enabled": false, "max": 10},
    "zalgo": {"enabled": false}
  },
  "logging": {
    "message_edit": true,
    "message_delete": true,
    "member_join": true,
    "member_leave": true,
    "role_change": true,
    "voice": true,
    "nickname": true,
    "ban": true,
    "unban": true,
    "kick": true,
    "channel": true,
    "emoji": true,
    "invite": true
  },
  "warning_punishments": {
    "3": "mute",
    "5": "kick",
    "7": "ban"
  },
  "raid_mode": false,
  "raid_threshold": 10,
  "raid_interval": 30,
  "min_account_age": 0
}
```

### Logging Events

| Event | Description |
|-------|-------------|
| `message_edit` | Messages being edited |
| `message_delete` | Messages being deleted |
| `member_join` | New members joining |
| `member_leave` | Members leaving |
| `role_change` | Role additions/removals |
| `voice` | Voice channel activity |
| `nickname` | Nickname changes |
| `ban` | Members banned |
| `unban` | Members unbanned |
| `kick` | Members kicked |
| `channel` | Channel create/delete/update |
| `emoji` | Emoji create/delete/update |
| `invite` | Invite create/delete |

</details>

---

## Self-Hosting

### Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Python | 3.10 | 3.11+ |
| Memory | 256MB | 512MB+ |
| CPU | 1 core | 2+ cores |
| Storage | 100MB | 500MB+ |

### Docker

Build and run with Docker:

```bash
# Build image
docker build -t vortex .

# Run container
docker run -d \
  --name vortex \
  -e DISCORD_TOKEN=your_token \
  -e GITHUB_TOKEN=your_token \
  -p 8080:8080 \
  vortex
```

### Docker Compose

```yaml
version: '3.8'

services:
  vortex:
    build: .
    container_name: vortex
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN=${DISCORD_TOKEN}
      - GITHUB_TOKEN=${GITHUB_TOKEN}
      - PORT=8080
    ports:
      - "8080:8080"
```

Run with:

```bash
docker-compose up -d
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | **Yes** | - | Your Discord bot token |
| `GITHUB_TOKEN` | **Yes** | - | GitHub token for data storage |
| `PORT` | No | `8080` | Health server port |

### Health Check

The bot runs a health server for monitoring:

```bash
curl http://localhost:8080/health
# Response: Vortex is running!
```

---

## License

This project is licensed under the MIT License.

```
MIT License

Copyright (c) 2024 Shebyyy

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## Support

| Platform | Link |
|----------|------|
| GitHub Issues | [Submit Issue](https://github.com/Shebyyy/vortex/issues) |
| Discord | [Join Server](https://discord.gg/) |

---

<div align="center">

**[Back to Top](#vortex)**

Developed by **Shebyyy**

</div>
