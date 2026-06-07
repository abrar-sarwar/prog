# prog

A custom Discord leveling bot for the progsu programming club, built to replace
MEE6.

prog rewards both **text** and **voice** activity, assigns a `progsuvian` base
role on first interaction, advances members through a club-themed tier ladder
(`Freshie → Viber → Innovator → Hustler → Yapper → Chud → Yappatron →
Challenger → Ascendant → Aura`), supports per-channel and per-role XP
multipliers, keeps a persistent **image leaderboard** that updates whenever the
standings change, welcomes new members, restores roles on rejoin, and runs a
`/daily` + `/leet` feature that hands out real LeetCode problems and grants XP
for verified solves.

XP earning is **opt-in per server**: a freshly-added guild starts with earning
OFF until an admin runs `/enable-xp`.

## Stack

- Python 3.14
- discord.py 2.7.1 (slash commands via `app_commands`, background work via `tasks`)
- SQLAlchemy 2.x (async) + asyncpg
- Supabase Postgres
- Alembic for migrations
- Pillow for the server-rendered rank card and leaderboard image

## macOS setup

```bash
# 1. Clone and enter the repo
cd path/to/prog

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

Set `ENV_FILE=.env.dev` to point both the bot and Alembic at an isolated test
database without touching the production `.env`.

When prog starts you should see something like:

```
loaded cog cogs.onboarding
loaded cog cogs.message_xp
...
synced N slash command(s) to guild <id>
connected as <bot> (<id>); serving 1 guild(s)
```

## First-time guild configuration

After the bot is running, run these from inside the test server. The bot's
role must sit **above** all the reward roles in the role list so it has the
permission to assign and remove them. The reward-role names below are the
recommended tier names, but `/setup-roles` maps a level threshold to whatever
role you point it at.

```
/enable-xp
/setup-progsuvian              role: @progsuvian
/setup-roles  level: 0         role: @top-of-leaderboard
/setup-roles  level: 1         role: @Freshie
/setup-roles  level: 10        role: @Viber
/setup-roles  level: 20        role: @Innovator
/setup-roles  level: 30        role: @Hustler
/setup-roles  level: 40        role: @Yapper
/setup-roles  level: 50        role: @Chud
/setup-roles  level: 60        role: @Yappatron
/setup-roles  level: 70        role: @Challenger
/setup-roles  level: 80        role: @Ascendant
/setup-roles  level: 100       role: @Aura
/setup-leaderboard-channel     channel: #leaderboard
/setup-levelup-channel         channel: #level-ups
/show-config
```

Optional, set as you like:

```
/set-channel-multiplier  channel: #project-chat  multiplier: 1.5
/set-role-multiplier     role: @booster          multiplier: 1.5
/blacklist-channel       channel: #bot-spam
/setup-channel-role      channel: #introductions role: @introduced
/setup-welcome-channel   channel: #welcome
/setup-leet              channel: #leetcode
/setup-leet-reward       role: @daily-solver
```

## Slash commands

### User

These require the `progsuvian` base role (assigned automatically on join /
first message / voice join). If a guild hasn't run `/setup-progsuvian` yet, the
gate fails open and anyone can use them.

| Command | What it does |
|---|---|
| `/rank [user]` | Server-rendered trophy card: tier, level, total XP, in-level progress, server rank, LeetCode stats. Defaults to the invoker. |
| `/leaderboard` | Paginated guild leaderboard, 10 per page. Footer always shows the invoker's own rank, even if off-page. |
| `/daily` | Today's official LeetCode daily challenge (same problem for everyone). Pays the streak-scaled multiplier once per UTC day; counts a solve done anytime today (UTC). Instant award if already solved before running the command. |
| `/leet [tag] [difficulty]` | Practice: a random free LeetCode problem. Reduced, capped XP (see below). Optional autocompleted topic tag and Easy/Medium/Hard difficulty (combine; neither = fully random). |
| `/leetverify <username>` | Link your LeetCode account so `/daily` and `/leet` can detect solves (drops a one-time `progsu-XXXX` code in your profile bio). |

### Admin (requires `Manage Server`)

| Command | What it does |
|---|---|
| `/enable-xp` / `/disable-xp` | Turn XP earning on/off for the server. |
| `/xp-give <user> <amount>` | Add XP. |
| `/xp-remove <user> <amount>` | Subtract XP (floored at 0). |
| `/xp-set <user> <amount>` | Set total XP, recompute level. |
| `/level-set <user> <level>` | Snap to the minimum XP for the target level. |
| `/setup-roles <level> <role>` | Map a level threshold to a role. `level=0` is the top-of-leaderboard role. |
| `/setup-progsuvian <role>` | Set the progsuvian base role. |
| `/setup-leaderboard-channel <channel>` | Where the persistent leaderboard image lives. |
| `/setup-levelup-channel <channel>` | Where level-up announcements are posted. |
| `/disable-levelup-message` | Stop posting level-up announcements (XP and roles still apply). |
| `/enable-levelup-message` | Resume posting level-up announcements. |
| `/blacklist-channel <channel>` | Toggle a channel's XP-blacklist status. |
| `/set-channel-multiplier <channel> <multiplier>` | Set or, with `1.0`, remove the channel's XP multiplier. |
| `/set-role-multiplier <role> <multiplier>` | Set or, with `1.0`, remove the role's XP multiplier. |
| `/setup-channel-role <channel> <role>` | Grant a role to anyone who talks in a channel (permanent, stacking, independent of XP). |
| `/remove-channel-role <channel>` | Stop a channel from granting its role. |
| `/setup-welcome-channel <channel>` | Opt in to welcome messages and set where they post. |
| `/setup-intro-channel <channel>` | Target of the `{intro_channel}` placeholder. |
| `/setup-welcome-message <message>` | Edit the welcome copy. |
| `/disable-welcome` | Stop posting welcomes (keeps saved text). |
| `/setup-leet <channel>` | Set the channel `/daily` and `/leet` run in (required before the feature works). |
| `/setup-leet-reward <role>` | Set a role granted for 24h on a confirmed solve. |
| `/approve-leet <user>` | Manually approve a member's active LeetCode session as solved. |
| `/leet-stats` | List members' LeetCode solve counts and streak stats. |
| `/reset-leet-stats [user]` | Reset LeetCode stats for a member or the whole server. |
| `/migrate-rank-roles` | Rename legacy ladder roles to the new tier names and remap thresholds. Safe to run repeatedly. |
| `/resync-roles` | Re-apply ladder roles to every member based on current level. |
| `/show-config` | Ephemeral dump of the current guild configuration. |
| `/force-leaderboard-update` | Rebuild and push the leaderboard image immediately. |

Admin commands appear greyed out for non-mods in the Discord UI; the in-handler
permission check is enforced server-side as well.

## How XP works

### Text XP

- Triggered by qualifying messages: not from a bot, not in a DM, ≥ 3 characters
  long, in a non-blacklisted channel, when the guild has XP enabled.
- Base XP per qualifying message: random 15-25.
- Per-user cooldown: 60 seconds (the first message inside the cooldown window
  is silently ignored).
- Multipliers apply: `final = int(base * channel_mult * highest_role_mult)`,
  floored at 1 if `base > 0`.

### Voice XP

- A background loop scans every voice channel every 60 seconds.
- A member earns voice XP if they are not a bot, share the channel with at least
  one other non-bot human, are not self/server muted or deafened, and the
  channel is not blacklisted.
- Base XP per qualifying tick: 10. Same multiplier formula as text XP.

### Multipliers

- **Channel multipliers** stack with role multipliers.
- **Role multipliers** never stack across roles - only the **highest** applicable
  role multiplier on the member is used.
- Setting a multiplier to `1.0` via the admin commands removes the entry.

### Leveling curve

The XP needed to clear level `n` is `n² + 30n + 50`, so level 1 → 81 XP, level
2 → 114, level 3 → 149, and so on. Total XP is cumulative; level is cached and
recomputed on every grant. The level cap is 100 (the "Aura" tier), whose
announcement fires exactly once per member. Pure functions live in
`core/leveling.py` and are covered by unit tests.

### Role rewards

When a level transition crosses a configured threshold, prog swaps the previous
ladder role for the new one. Reward roles come from the per-guild `role_rewards`
table set via `/setup-roles`; the tier names above are the recommended labels.
The `progsuvian` base role is **never** removed - it stays on every member who
has ever interacted.

Admin commands that change levels (`/xp-give`, `/xp-remove`, `/xp-set`,
`/level-set`) dispatch the same `level_change` event, so demotions are handled
correctly too.

## Leaderboard (change-driven image)

prog keeps one message in the configured leaderboard channel bearing a PNG of
the current top 10, rendered server-side with Pillow.

- On startup it caches each guild's current top-N to avoid a spurious
  "everything changed" edit on the first event after a restart.
- After every XP grant the XP cogs dispatch `xp_grant(guild_id)`; the
  leaderboard listener re-queries the top-N and, if the order changed, schedules
  a short **debounced** edit so a burst of level-ups produces one render, not a
  storm.
- A periodic **safety poll** re-checks every configured guild in case a dispatch
  was missed (e.g. a restart between dispatch and edit) and to pick up avatar
  changes that don't move the rankings.
- The top-of-leaderboard role (`role_rewards.level == 0`) is reassigned to #1
  whenever the standings change.
- `/force-leaderboard-update` bypasses the cache and pushes a fresh render.

## /daily and /leet (LeetCode website-solve feature)

Verified members solve real LeetCode problems on leetcode.com and the bot
detects their accepted submission by polling their **public** profile - no
screenshots, no pasted code, no OAuth.

### /daily - official daily challenge

`/daily` gives each member today's official LeetCode daily problem (the same
problem for everyone each UTC day).

- Pays the **streak-scaled multiplier**: `LEET_REWARD_BASE_FRACTION` to
  `LEET_REWARD_CAP_FRACTION` of the XP cost to reach the next level, scaled by
  the member's consecutive-UTC-day streak up to `LEET_REWARD_STREAK_MILESTONE_DAYS`.
- One per UTC day: a second `/daily` the same day is blocked.
- If the member already solved today's daily on leetcode.com before running
  `/daily`, the bot detects it instantly and awards without opening a thread.
- Detection rule: the slug must appear in the recent accepted list with a
  timestamp at or after UTC midnight today (`find_daily_solve_today`).

### /leet - practice

`/leet [tag] [difficulty]` gives a random free problem, optionally scoped to a
topic tag (autocompleted) and/or an Easy/Medium/Hard difficulty (they combine).
With neither argument it pulls a random free problem from the pool. An
impossible combination replies "no free problems match ... try a different combo".

- Pays a **reduced, capped rate**: the first `LEET_PRACTICE_DAILY_CAP` practice
  solves of the UTC day each pay `LEET_PRACTICE_FRACTION` of the next level's XP
  cost. Further practice solves that day pay the flat `LEET_PRACTICE_OVERFLOW_XP`
  token.
- Detection rule: the assigned slug must appear in the recent accepted list with
  a timestamp **after** the assignment, so a pre-existing solve never counts
  (`find_matching_submission`).

### Streak rule

Any confirmed solve - daily or practice - keeps the consecutive-UTC-day streak
alive and increments the solved total. Only `/daily` pays the streak-scaled
multiplier; `/leet` never does.

### Shared behavior

- `/leetverify <username>` links an account by dropping a one-time `progsu-XXXX`
  code in the LeetCode profile bio.
- Only one active session at a time (daily or practice).
- The problem pool is built from every free (non-premium) problem and refreshed
  periodically; a committed snapshot keeps it populated between refreshes.
- An optional reward role (`/setup-leet-reward`) is granted for 24h on a solve;
  re-earning refreshes the timer rather than stacking. A background sweep
  removes expired grants and reaps stale sessions.

All timing and reward knobs live in `core/constants.py`.

### Manual verification (/daily + /leet)

Run these against a dev guild after `alembic upgrade head` (migration
`f3b9c1d4e2a7` adds `leetcode_assignments.kind` and `daily_date`):

- [ ] `/daily` opens a thread with today's LeetCode daily; solving it on
  leetcode.com gets detected and pays the multiplier; streak advances.
- [ ] Running `/daily` again the same day is blocked ("already done today's
  daily").
- [ ] If you solved today's daily before running `/daily`, it awards instantly
  with no thread.
- [ ] `/leet` with no tags pays the reduced practice rate; after
  `LEET_PRACTICE_DAILY_CAP` practice solves on the same UTC day the payout
  drops to the `LEET_PRACTICE_OVERFLOW_XP` token.
- [ ] `/leet tag:dynamic-programming difficulty:Hard` autocompletes and yields a
  hard DP problem; an impossible combo replies "no free problems match ... try a
  different combo".
- [ ] A practice solve on a fresh day keeps the streak alive (streak count
  increments).

## Project layout

```
main.py                       bot entrypoint, logging, cog loader
config.py                     .env loading (honors ENV_FILE)
core/                         pure, unit-tested logic (no Discord I/O)
  leveling.py                 XP <-> level math, tier ladder, level-up copy
  multipliers.py              channel + role multiplier composition
  constants.py                XP rates, cooldowns, leaderboard + /leet knobs
  roles.py                    role-restore filtering
  welcome.py                  default welcome message
  rank_image.py               /rank trophy-card renderer (Pillow)
  leaderboard_image.py        leaderboard PNG renderer (Pillow)
  leetcode_client.py          LeetCode GraphQL/HTTP client
  leetcode_problems.py        problem pool
  leetcode.py                 pure payout + submission-match logic
db/
  engine.py                   async engine + session factory singletons
  models.py                   SQLAlchemy ORM (users, guild_config, role_rewards, leetcode_*)
  crud.py                     ALL DB reads/writes (single chokepoint)
cogs/                         Discord glue
  onboarding.py               on_member_join/remove, ensure_member_initialized
  message_xp.py               on_message text XP grant
  voice_xp.py                 1-minute voice tick
  rewards.py                  level_change -> ladder role transition
  commands.py                 /rank, /leaderboard
  admin.py                    all admin slash commands
  leaderboard_channel.py      change-driven leaderboard image + /force-leaderboard-update
  leet.py                     /daily, /leet, /leetverify + leet admin commands
  checks.py                   requires_progsuvian app-command gate
migrations/                   Alembic versions
tests/                        pytest unit tests for the pure-logic modules
```

**Architectural rule:** cogs talk to the database only through `db/crud.py`.
No inline `session.execute(...)` in cog code.

## Architecture notes

- **Level-up notifications.** Routed to `guild_config.level_up_channel_id` if
  set; else (text XP) the originating channel; else (voice XP) the guild's
  `system_channel`; else skipped silently.
- **Custom event `level_change`.** Dispatched by every XP/level mutation
  (message, voice, admin commands, `/leet` payout). The rewards cog listens on
  it and runs the ladder transition. Decouples rewards from the XP sources.
- **Concurrency safety.** XP mutators take a `SELECT ... FOR UPDATE` lock on
  the user row so concurrent text + voice grants on the same user serialise
  cleanly and never lose updates. CRUD functions never commit; callers own the
  transaction.
- **JSONB columns.** Channel and role multiplier dicts use string keys
  (`{"123": 1.5}`) because JSON keys are always strings. The CRUD layer
  normalises ints on the read/write boundary.
- **Connection pooling.** `db/engine.py` keeps a small bounded pool and detects
  the Supabase Supavisor transaction pooler (`:6543`) to disable prepared
  statements there. The whole project shares a small Supavisor budget, so pool
  sizes are intentionally conservative.

## Running tests

```bash
pytest                                # full suite
pytest tests/test_leveling.py         # one file
pytest tests/test_leveling.py::name   # one test
```

Tests cover the pure-logic `core/` modules (leveling, multipliers, roles,
welcome, leetcode payout/match, shared checks). Database and Discord-side
behavior are exercised live against the test guild. `conftest.py` puts the repo
root on `sys.path` (there is no `pyproject.toml`/`setup.py`).

## Operations notes

- Slash commands register as **guild** commands against `TEST_GUILD_ID`, so
  changes appear instantly during development. For production rollout to other
  servers, switch to a global sync (slow propagation).
- All timestamps stored in the database are timezone-aware UTC.
- The connection string keeps its `+asyncpg` suffix; SQLAlchemy needs it. If
  raw asyncpg ever needs the plain `postgresql://` form, strip it locally in
  that one function, never globally.
- On graceful shutdown, `ProgBot.close()` disposes the SQLAlchemy engine's
  connection pool before delegating to the base class.
- The bot survives restarts cleanly: every piece of state lives in Postgres and
  Alembic owns the schema.
