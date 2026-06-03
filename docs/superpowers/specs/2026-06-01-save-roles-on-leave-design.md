# Save roles on leave, restore on rejoin — design

## Summary

When a member leaves a guild, snapshot the roles they had into their existing
`users` row (which already persists by Discord ID). When they rejoin, re-apply
those roles. Level/XP already persist; this adds role persistence so a member
comes back "the way they were."

Scope decision: restore **all roles except** those that are unassignable by a
bot (managed/integration/booster roles, `@everyone`) or that carry **dangerous
permissions** (Administrator, Manage Server/Roles/Channels, Ban, Kick). This
prevents a member regaining mod/admin power just by leaving and rejoining.

## Storage

Add `saved_role_ids` to the `users` table (`db/models.py`):

- `saved_role_ids: JSONB, NOT NULL, server_default '[]'` — list of role IDs
  (ints) the member had at their last departure.

Alembic migration branches from head `d5a7b30f9c12`. `upgrade` adds the column
(default `[]`, so every existing row is valid with no backfill); `downgrade`
drops it.

## On leave — `on_member_remove` (new listener, onboarding cog)

1. Skip bots.
2. Build the snapshot from `member.roles`, keeping a role only if
   `core.roles.role_is_restorable(...)` is True given:
   - `is_default` = `role.is_default()` (the `@everyone` role)
   - `is_managed` = `role.managed`
   - `has_dangerous_perm` = `has_dangerous_permission(...)` over the role's perms
   - `is_above_bot` = False at snapshot time (position is only meaningful at
     restore time; we store regardless and re-filter on the way back in)
3. Persist via `crud.set_saved_roles(session, guild_id, member.id, ids)`.

## On rejoin — `on_member_join` (after existing init)

Runs after `ensure_member_initialized` and `post_welcome_message`.

1. Skip bots.
2. Load the `users` row; read `saved_role_ids`. If empty, nothing to do.
3. For each saved ID, resolve the role and re-filter with
   `role_is_restorable`, now including `is_above_bot` (compare role.position to
   the guild's `me.top_role.position`). Re-checking dangerous perms here is
   defense-in-depth: a role's permissions may have been elevated while the
   member was away.
4. Add all survivors in a single `member.add_roles(*roles, reason=...)` call.
   Wrap in the cog's existing `discord.Forbidden` / `discord.HTTPException`
   warning logging.
5. Leave the snapshot in place — it is overwritten on the next departure, so a
   partial/failed restore is not lost.

## Pure logic — `core/roles.py` (new, no Discord imports, unit-tested)

```
DANGEROUS_PERMISSIONS = (
    "administrator", "manage_guild", "manage_roles",
    "manage_channels", "ban_members", "kick_members",
)

def has_dangerous_permission(perm_names) -> bool:
    """True if any granted permission name is in DANGEROUS_PERMISSIONS."""

def role_is_restorable(*, is_default, is_managed,
                       has_dangerous_perm, is_above_bot) -> bool:
    """A role is restorable iff it is none of: default(@everyone),
    managed, dangerous, or above the bot's top role."""
    return not (is_default or is_managed or has_dangerous_perm or is_above_bot)
```

The cog supplies the Discord-specific booleans; all branching is pure/tested.

## CRUD — `db/crud.py`

`set_saved_roles(session, guild_id, user_id, role_ids: list[int]) -> User` —
get-or-create the row and reassign `saved_role_ids` (reassign, not mutate, so
SQLAlchemy detects the JSONB change). Caller commits.

## Tests — `tests/test_roles.py`

- `role_is_restorable` truth table (each exclusion flag independently blocks).
- `has_dangerous_permission`: admin/manage roles → True; plain color role → False;
  empty → False.

## Known limitations (noted in code)

- If the bot is offline when a member leaves, `on_member_remove` never fires, so
  that departure is not snapshotted.
- Roles above prog's own role and managed/booster roles cannot be restored by a
  bot (Discord rule) and are skipped.
- Dangerous-permission roles are intentionally excluded per the scope decision.

## Files touched

- `db/models.py` — `saved_role_ids` column
- `migrations/versions/<new>.py` — add/drop the column
- `core/roles.py` — new pure helpers
- `db/crud.py` — `set_saved_roles`
- `cogs/onboarding.py` — `on_member_remove` snapshot + restore in `on_member_join`
- `tests/test_roles.py` — new
