# prog

A custom Discord leveling bot for a programming club, built to replace MEE6.

prog rewards both **text** and **voice** activity, assigns a `progsuvian` base
role on first interaction, advances members through a club-themed level ladder
(`prognoob → progknight → gitsworn → progmaster → gitalife`), supports
per-channel and per-role XP multipliers, and posts a weekly Saturday-noon
leaderboard (Eastern time) that freezes after **August 15, 2026**.

XP itself does **not** freeze on Aug 15 — members keep leveling up forever.
Only the public top-10 channel post and the top-of-leaderboard role
assignment stop updating.

## Stack

- Python 3.14
- discord.py 2.7.1 (slash commands via `app_commands`, background work via `tasks`)
- SQLAlchemy 2.x (async) + asyncpg
- Supabase Postgres 17.6
- Alembic for migrations
- `zoneinfo` (stdlib) for Eastern-time scheduling

## macOS setup

```bash
# 1. Clone and enter the repo
cd ~/dev/prog

# 2. Create and activate a Python 3.14 venv (Homebrew Python 3.14)
python3.14 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the env template and fill in real values
cp .env.example .env
# Then edit .env:
#   DISCORD_TOKEN  - from the Discord Developer Portal
#   TEST_GUILD_ID  - your dev server's ID (Developer Mode -> Copy Server ID)
#   DATABASE_URL   - Supabase Postgres URL, MUST start with postgresql+asyncpg://

# 4a. In the Discord Developer Portal -> your app -> Bot tab, enable:
#       - "Server Members Intent"
#       - "Message Content Intent"
#     The bot will fail to start without them.

# 5. Apply the database migrations
alembic upgrade head

# 6. Run the bot
python main.py
```

When prog starts you should see something like:

```
loaded cog cogs.onboarding
loaded cog cogs.message_xp
...
synced 15 slash command(s) to guild <id>
connected as <bot>#... ; serving 1 guild(s)
```

## First-time guild configuration

After the bot is running, run these from inside the test server. The bot's
role must sit **above** all the reward roles in the role list so it has the
permission to assign and remove them.

```
/setup-progsuvian              role: @progsuvian
/setup-roles  level: 0         role: @top-of-leaderboard
/setup-roles  level: 1         role: @prognoob
/setup-roles  level: 11        role: @progknight
/setup-roles  level: 21        role: @gitsworn
/setup-roles  level: 31        role: @progmaster
/setup-roles  level: 41        role: @gitalife
/setup-leaderboard-channel     channel: #leaderboard
/setup-levelup-channel         channel: #level-ups
/show-config
```

Optional, set as you like:

```
/set-channel-multiplier  channel: #project-chat  multiplier: 1.5
/set-role-multiplier     role: @booster          multiplier: 1.5
/blacklist-channel       channel: #bot-spam
```

## Slash commands

### User

| Command | What it does |
|---|---|
| `/rank [user]` | Embed with level, total XP, in-level progress bar, server rank. Defaults to the invoker. |
| `/leaderboard` | Paginated guild leaderboard, 10 per page. Footer always shows the invoker's own rank, even if they're off-page. |
| `/leet` | Initiates a daily LeetCode challenge in the `#programming` channel, creating a public thread and prompting with a random question. |
| `/leet-status [user]` | Shows a user's current streak, total solved challenges, active question/thread link, and remaining daily XP boost duration. |

### Admin (requires `Manage Server`)

| Command | What it does |
|---|---|
| `/xp-give <user> <amount>` | Add XP. |
| `/xp-remove <user> <amount>` | Subtract XP (floored at 0). |
| `/xp-set <user> <amount>` | Set total XP, recompute level. |
| `/level-set <user> <level>` | Snap to the minimum XP for the target level. |
| `/setup-roles <level> <role>` | Map a level threshold to a role. `level=0` is the top-of-leaderboard role. |
| `/setup-progsuvian <role>` | Set the progsuvian base role. |
| `/setup-leaderboard-channel <channel>` | Where the weekly leaderboard is posted. |
| `/setup-levelup-channel <channel>` | Where level-up announcements are posted. |
| `/blacklist-channel <channel>` | Toggle a channel's XP-blacklist status. |
| `/set-channel-multiplier <channel> <multiplier>` | Set or, with `1.0`, remove the channel's XP multiplier. |
| `/set-role-multiplier <role> <multiplier>` | Set or, with `1.0`, remove the role's XP multiplier. |
| `/show-config` | Ephemeral dump of the current guild configuration. |
| `/force-leaderboard-update` | Run the weekly update immediately. Still respects the freeze date. |
| `/leet-approve <user>` | Force-approves the selected user's active LeetCode daily challenge immediately and archives the thread. |
| `/leet-modify <user> [streak] [total] [reset_daily]` | Set a user's streak or total completed challenges, or reset their daily completion state to allow a new question. |
| `/leet-analytics <period>` | View stats on daily challenge threads opened, completed, and expired over the last day, week, or month. |

Admin commands appear greyed out for non-mods in the Discord UI; the in-handler
permission check is enforced server-side as well.

## How XP works

### Text XP

- Triggered by qualifying messages: not from a bot, not in a DM, ≥ 3 characters
  long, in a non-blacklisted channel.
- Base XP per qualifying message: random 15–25.
- Per-user cooldown: 60 seconds (the first message inside the cooldown window
  is silently ignored).
- Multipliers apply: `final = int(base * channel_mult * highest_role_mult)`,
  floored at 1 if `base > 0`.

### Voice XP

- A background loop scans every voice channel every 60 seconds.
- A member earns voice XP if:
  - They are not a bot.
  - They share the channel with at least one other non-bot human.
  - They are not self-muted, server-muted, self-deafened, or server-deafened.
  - The channel is not blacklisted.
- Base XP per qualifying tick: 10.
- Same multiplier formula as text XP.

### Multipliers

- **Channel multipliers** stack with role multipliers.
- **Role multipliers** never stack across roles — only the **highest** applicable
  role multiplier on the member is used.
- Setting a multiplier to `1.0` via the admin commands removes the entry.

### Leveling curve

The XP needed to clear level `n` is `n² + 30n + 50`, so level 1 → 81 XP, level
2 → 114, level 3 → 149, and so on. Total XP is cumulative; level is cached and
recomputed on every grant. Pure functions live in `core/leveling.py` and are
covered by unit tests.

### Role rewards

When a level transition crosses a configured threshold, prog swaps the previous
ladder role for the new one (e.g. at level 11, `prognoob` is removed and
`progknight` is added). The `progsuvian` base role is **never** removed — it
stays on every member who has ever interacted.

Admin commands that change levels (`/xp-give`, `/xp-remove`, `/xp-set`,
`/level-set`) dispatch the same transition event, so demotions are handled
correctly too.

## Weekly leaderboard + freeze

- A `tasks.loop(minutes=1)` checks once per minute whether it is Saturday
  between 12:00 and 12:01 PM `America/New_York`. `zoneinfo` handles EDT/EST
  switching automatically.
- When the trigger fires, prog edits the message at
  `guild_config.leaderboard_message_id` (or sends a new one if missing or
  deleted) with the current top 10, then reassigns the role registered at
  `role_rewards.level = 0` to whoever is currently #1.
- **Freeze date** is `2026-08-15` (a Saturday). On that day prog runs **one
  final update** with the `🏆 Final standings — leaderboard frozen` footer.
  All later Saturdays are skipped: XP keeps accruing, `/rank` and level-ups
  keep working, but the public post and top-of-leaderboard role do not move.
- `/force-leaderboard-update` runs the same logic out of band for testing.
  It still respects the freeze date.

## Project layout

```
main.py                       bot entrypoint, logging, cog loader
config.py                     .env loading
core/
  leveling.py                 pure XP <-> level math
  multipliers.py              channel + role multiplier composition
  constants.py                rates, cooldowns, role names, FREEZE_DATE
db/
  engine.py                   async engine + session factory singletons
  models.py                   SQLAlchemy ORM (users, guild_config, role_rewards)
  crud.py                     ALL DB reads/writes (single chokepoint)
cogs/
  onboarding.py               on_member_join + ensure_member_initialized
  message_xp.py               on_message text XP grant
  voice_xp.py                 1-minute voice tick
  commands.py                 /rank, /leaderboard
  admin.py                    all admin slash commands
  rewards.py                  level_change -> ladder role transition
  leaderboard_channel.py      weekly Saturday-noon update + freeze + /force-leaderboard-update
migrations/                   Alembic versions
tests/                        pytest unit tests for pure-logic modules
```

**Architectural rule:** cogs talk to the database only through `db/crud.py`.
No inline `session.execute(...)` in cog code.

## Architecture notes

- **Level-up notifications.** Routed to `guild_config.level_up_channel_id` if
  set; else (text XP) the originating channel; else (voice XP) the guild's
  `system_channel`; else skipped silently.
- **Custom event `level_change`.** Dispatched by every XP/level mutation
  (message, voice, admin commands). The rewards cog listens on it and runs
  the ladder transition. Decouples rewards from the XP sources.
- **Concurrency safety.** XP mutators take a `SELECT ... FOR UPDATE` lock on
  the user row so concurrent text + voice grants on the same user serialise
  cleanly and never lose updates.
- **JSONB columns.** Channel and role multiplier dicts use string keys
  (`{"123": 1.5}`) because JSON keys are always strings. The CRUD layer
  normalises ints on the read/write boundary.

## Running tests

```bash
pytest
```

20 tests cover the leveling curve and the XP-multiplier function. Database
and Discord-side behavior are exercised live against the test guild.

## Operations notes

- Slash commands register as **guild commands** against `TEST_GUILD_ID`, so
  changes appear instantly during development. For production rollout to other
  servers, switch to a global sync (slow propagation).
- All timestamps stored in the database are timezone-aware UTC. Eastern time
  (`America/New_York`) is used only by the Saturday-noon leaderboard scheduler
  so daylight-saving is handled correctly.
- The connection string keeps its `+asyncpg` suffix; SQLAlchemy needs it. If
  raw asyncpg ever needs the plain `postgresql://` form, strip it locally in
  that one function, never globally.
- On graceful shutdown, `ProgBot.close()` disposes the SQLAlchemy engine's
  connection pool before delegating to the base class.
- The bot survives restarts cleanly: every piece of state lives in Postgres
  (`users`, `guild_config`, `role_rewards`) and Alembic owns the schema.
