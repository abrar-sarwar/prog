# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`prog` is a Discord leveling bot for the progsu programming club, built to replace MEE6. It rewards text + voice activity, runs a club-themed 100-level tier ladder, keeps a persistent image leaderboard, and runs a `/leet` LeetCode-solve feature that grants XP for real accepted submissions. Python 3.14, discord.py 2.7.1 (slash commands via `app_commands`, background work via `tasks`), async SQLAlchemy 2.x + asyncpg against Supabase Postgres, Alembic for migrations.

Note: `README.md` is partly stale (it describes a *weekly Saturday-noon* leaderboard and omits the opt-in XP gate, channel-roles, welcome-on-join, and the rewritten `/leet` feature). Trust the code over the README; this file reflects current behavior.

## Commands

```bash
source venv/bin/activate         # Homebrew Python 3.14 venv
pip install -r requirements.txt
cp .env.example .env             # fill DISCORD_TOKEN, TEST_GUILD_ID, DATABASE_URL
alembic upgrade head             # apply migrations
python main.py                   # run the bot

pytest                           # full suite
pytest tests/test_leveling.py    # single file
pytest tests/test_leveling.py::test_name   # single test
```

`DATABASE_URL` **must** keep the `postgresql+asyncpg://` driver qualifier. Set `ENV_FILE=.env.dev` to point both the bot and Alembic at an isolated test database (`config.py` and `migrations/env.py` both honor it). Both privileged intents (Server Members, Message Content) must be enabled in the Discord Developer Portal or the bot won't start.

### Migrations

```bash
alembic revision --autogenerate -m "description"   # diff ORM vs db
alembic upgrade head
alembic downgrade -1
```

`migrations/env.py` injects `DATABASE_URL` from the env into Alembic and imports `db.models.Base` for autogenerate, so `alembic.ini`'s `sqlalchemy.url` is intentionally blank. ORM model changes in `db/models.py` are the source of truth; generate and review a migration for every schema change.

## Architecture

### The CRUD chokepoint (the one rule to internalize)

**Cogs must never call `session.execute(...)` directly. All DB access goes through `db/crud.py`.** This is the single place for query optimization, auditing, and locking. When adding behavior that touches the database, add a function to `db/crud.py` and call it from the cog.

Transaction discipline: CRUD functions **do not commit**. Callers own the transaction (`async with session_factory() as session: ...; await session.commit()` or `session_factory.begin()`). Every XP/level mutator takes a `SELECT ... FOR UPDATE` row lock so concurrent text + voice grants on the same user serialize and never lose updates.

`db/engine.py` exposes lazy module-level singletons (`get_engine`, `get_session_factory`, `dispose_engine`). The pool is deliberately small and pool-aware: it detects the Supabase Supavisor transaction pooler (`:6543`) and switches to `statement_cache_size=0` + tiny pool, because the whole project shares a ~15-client Supavisor budget. Do not bump pool sizes casually. `ProgBot.close()` calls `dispose_engine()` on shutdown.

### The `level_change` event (decouples XP sources from rewards)

Every XP mutation returns a `crud.LevelChange` (`user`, `old_level`, `new_level`, `.leveled_up`). XP sources (`message_xp`, `voice_xp`, admin commands, `/leet` payout) all dispatch a custom `level_change` Discord event. `cogs/rewards.py` is the sole listener; it runs the ladder role transition (swap previous tier role for the new one). This means new XP sources get rewards for free by dispatching the same event - never inline role logic into an XP source.

Level-up announcement routing: `guild_config.level_up_channel_id` if set, else the originating text channel, else (voice) the guild `system_channel`, else skipped. The level-100 "Aura" message fires exactly once per (guild, user), guarded by `users.aura_message_fired`.

### Pure logic in `core/`, Discord glue in `cogs/`

`core/` modules are side-effect-free and unit-tested; `cogs/` modules hold all Discord I/O.

- `core/leveling.py` - the XP curve (`xp_for_level(n) = n² + 30n + 50`), `cumulative_xp_to_level`, `level_from_total_xp` (capped at `LEVEL_CAP = 100`), the `TIER_BANDS` ladder (Freshie → Viber → ... → Aura), per-tier `LEVEL_MESSAGES`, and the `admin_rank_change_action` pure helper. Import leveling math from here, not `constants.py`.
- `core/multipliers.py` - composition: `final = int(base * channel_mult * highest_role_mult)`. Channel mults stack with role mults; role mults **never** stack across roles (highest applicable only).
- `core/constants.py` - tunable plain-data values only (XP rates, cooldowns, leaderboard debounce, all `LEET_*` timing/reward knobs). No per-guild data (that lives in the DB).
- `core/roles.py`, `core/welcome.py` - role-restore filtering and the default welcome message.
- `core/rank_image.py`, `core/leaderboard_image.py` - Pillow renderers for the `/rank` card and the leaderboard PNG (fonts in `assets/fonts/`, template in `assets/`).
- `core/leetcode*.py` - the LeetCode integration split into `leetcode_client.py` (GraphQL/HTTP), `leetcode_problems.py` (problem pool), `leetcode.py` (pure payout/match logic: `compute_solve_payout`, `find_matching_submission`).

### Leaderboard is change-driven, not scheduled

`cogs/leaderboard_channel.py` keeps one message (with a rebuilt PNG attachment) in sync with the live top-N. After each XP grant, `message_xp`/`voice_xp` dispatch `xp_grant(guild_id)`; the listener re-queries the top-N, compares order-sensitively to an in-memory cache (`_cache`), and schedules a debounced edit (`LEADERBOARD_DEBOUNCE_SECONDS`). A safety poll (`LEADERBOARD_SAFETY_POLL_MINUTES`) re-checks all guilds to catch dropped events and avatar changes. Editing a message with an attachment requires re-uploading the PNG, so the cache check exists to avoid edit storms. The top-of-leaderboard role (`role_rewards.level == 0`) is reassigned whenever the top-N changes. `/force-leaderboard-update` bypasses the cache.

### Onboarding and gating

`cogs/onboarding.py` owns `ensure_member_initialized` - it creates the `users` row and assigns the `progsuvian` base role on join / first message / voice join. Other cogs (e.g. `leet`) call it to guarantee a member is initialized. The `progsuvian` role is never removed.

`cogs/checks.py` provides `requires_progsuvian()`, the app-command check that gates member-facing commands. It **fails open** when the guild has no progsuvian role configured or the interaction is outside a guild - never lock a server out before `/setup-progsuvian` runs. Failure raises `ProgsuvianRequired`, rendered as a friendly ephemeral by each cog's `cog_app_command_error`.

XP earning is **opt-in per guild** (`guild_config.xp_enabled`, default false; enabled via `/enable-xp`). The migration flips already-active guilds to true.

### Cog registry

`main.py` `COGS` list is the explicit load order. Unfinished cogs are intentionally omitted (importing a broken extension crashes startup). Slash commands sync as **guild** commands to `TEST_GUILD_ID` for instant dev iteration (`copy_global_to` + `tree.sync`); production rollout would switch to global sync.

## Data model (`db/models.py`)

- `users` - per-(guild, user) cumulative `xp` + cached `level` (keep in sync; `level` is a cache of `level_from_total_xp(xp)`), plus voice/text totals, `aura_message_fired`, `saved_role_ids` (re-applied on rejoin), and `/leet` stats (`leetcode_solved_total`, `leetcode_streak`, `leetcode_last_solve_date`).
- `guild_config` - all per-guild settings (channels, roles, `xp_enabled`, multipliers, channel-roles, welcome, leetcode channel/reward role).
- `role_rewards` - level→role map; `level == 0` is the top-of-leaderboard role.
- `leetcode_links` / `leetcode_assignments` / `leetcode_role_grants` - the `/leet` feature (verified account link, per-session lifecycle, timed reward-role grants).

JSONB conventions: dict-keyed columns (`channel_multipliers`, `role_multipliers`, `channel_roles`) store **string keys** because JSON keys are always strings; `db/crud.py` normalizes int↔str on the read/write boundary. All stored timestamps are tz-aware UTC; `America/New_York` / `zoneinfo` is used only where Eastern-time presentation matters.

## Conventions

- Comments are minimal and lowercase; module docstrings carry the heavy explanation (they are thorough - read them).
- Tests cover pure-logic `core/` modules only (no pytest-asyncio; DB/Discord behavior is exercised live against the test guild). Add/update tests in `tests/` for any `core/` change. `conftest.py` puts the repo root on `sys.path` (no `pyproject.toml`/`setup.py`).
- Keep the `+asyncpg` suffix on `DATABASE_URL`; strip it locally only in the rare spot that needs raw asyncpg, never globally.
