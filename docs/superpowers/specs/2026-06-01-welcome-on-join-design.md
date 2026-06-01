# Welcome-on-join — design

## Summary

When a non-bot member joins a guild, post a configurable welcome message to a
configurable channel. The message text is editable per-guild (with a sensible
default), supports `{user}`, `{server}`, and `{intro_channel}` placeholders, and
links to a configurable "introduce yourself" channel. The feature is opt-in:
nothing is posted until an admin sets a welcome channel.

This is purely additive. The existing join behavior is unchanged:
`ensure_member_initialized` still assigns the progsuvian base role and creates
the level-0 `users` row. No rank/level-up message fires at join (level 0 has no
tier template); the normal Freshie message still fires when the member reaches
level 1 through the XP path.

## Behavior

On `on_member_join` (in `cogs/onboarding.py`):

1. `ensure_member_initialized(member)` runs first (unchanged) — progsuvian role
   + `users` row. Bots are skipped.
2. If the member is a bot, stop (no welcome).
3. Read the guild config. If `welcome_channel_id` is `None`, stop silently
   (opt-in).
4. Resolve the welcome channel. If the configured channel no longer exists or
   isn't a text-capable channel, log a warning and stop.
5. Resolve the intro-channel mention from `welcome_intro_channel_id`:
   - configured and found → its `.mention` (clickable `#channel`)
   - not configured or missing → the literal fallback string
     `"the intro channel"`
6. Render the message with `core.welcome.render_welcome` using the per-guild
   `welcome_message` override, or `DEFAULT_WELCOME_MESSAGE` when the override is
   `None`.
7. Send to the welcome channel with
   `allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False)`.
   Never `@everyone`/`@here`. Wrap in the same `discord.Forbidden` /
   `discord.HTTPException` try/except + warning logging the cog already uses for
   role assignment.

## Schema

Add three nullable columns to `guild_config` (`db/models.py`) plus an Alembic
migration branching from the current head `c3f9a2d18e45`:

- `welcome_channel_id: BigInteger, nullable=True` — where the welcome posts.
  Presence of a value is what turns the feature on.
- `welcome_intro_channel_id: BigInteger, nullable=True` — target channel for the
  `{intro_channel}` link.
- `welcome_message: Text, nullable=True` — per-guild message override. `NULL`
  means "use `DEFAULT_WELCOME_MESSAGE` from code".

The migration is `add_column` for all three (no backfill needed; `NULL` is the
correct default for every existing guild — feature stays off until opted in).
`downgrade` drops the three columns.

## Pure logic — `core/welcome.py` (new, unit-tested, no Discord imports)

```
DEFAULT_WELCOME_MESSAGE = (
    "welcome {user}! take a look at what we offer here in the {server} "
    "server and introduce yourself in {intro_channel}"
)

def render_welcome(template, *, user, server, intro_channel) -> str:
    """Substitute {user}, {server}, {intro_channel} into template.

    Safe against admin-edited text: unknown placeholders and malformed/
    unbalanced braces never raise — they are left as-is rather than crashing
    the join handler.
    """
```

Implementation note: do NOT use bare `str.format` — admin text may contain
stray `{` / `}` or unknown keys, which would raise `KeyError`/`ValueError`/
`IndexError`. Use a substitution that only replaces the three known
placeholders and leaves everything else (including stray braces) untouched.
A small mapping-based replace (e.g. iterating the three known tokens) is the
intended approach.

## CRUD — `db/crud.py`

Three setters following the existing `get_or_create_guild_config` pattern
(no commit inside; caller commits):

- `set_welcome_channel(session, guild_id, channel_id: int | None) -> GuildConfig`
- `set_welcome_intro_channel(session, guild_id, channel_id: int | None) -> GuildConfig`
- `set_welcome_message(session, guild_id, message: str | None) -> GuildConfig`

## Admin commands — `cogs/admin.py`

All `@app_commands.guild_only()`, `default_permissions(manage_guild=True)`,
`checks.has_permissions(manage_guild=True)`, matching the existing commands.

- `/setup-welcome-channel <channel>` — set where welcomes post (opts the
  feature in). Confirmation embed; if no welcome message override is set, a tip
  mentions `/setup-welcome-message` and the default is in effect.
- `/setup-intro-channel <channel>` — set the `{intro_channel}` target.
- `/setup-welcome-message` — opens a **modal** (`discord.ui.Modal` with a
  paragraph-style `discord.ui.TextInput`) prefilled with the current override or
  the default. On submit, saves via `set_welcome_message`. Empty submission
  clears the override (reverts to default).
- `/disable-welcome` — clears `welcome_channel_id` (keeps the saved message
  text). Confirmation embed.

`/show-config` gains a "Welcome" field showing: welcome channel, intro channel,
and whether a custom message is set ("custom" vs "default").

## Tests — `tests/test_welcome.py`

Pure-function tests for `render_welcome`:

- default template renders all three placeholders correctly
- a custom template using a subset of placeholders renders correctly
- `{intro_channel}` fallback string is used when intro channel is absent
- malformed/unbalanced braces and unknown placeholders in admin text do not
  raise and are left intact

## Out of scope / non-goals

- No welcome DMs (channel post only).
- No welcome images/embeds — plain content message, consistent with the
  existing level-up announcement style.
- No `@everyone`/`@here` ever (see project feedback memory).
- No change to XP, leveling, or the Freshie level-1 message.

## Files touched

- `db/models.py` — three new columns
- `migrations/versions/<new>.py` — add/drop the three columns
- `core/welcome.py` — new, default text + `render_welcome`
- `db/crud.py` — three setters
- `cogs/onboarding.py` — post welcome in `on_member_join`
- `cogs/admin.py` — four commands + modal + `/show-config` field
- `tests/test_welcome.py` — new
