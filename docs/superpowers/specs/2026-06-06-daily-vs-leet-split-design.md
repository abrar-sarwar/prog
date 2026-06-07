# /daily vs /leet: daily-challenge multiplier + tag-scoped practice

**Date:** 2026-06-06
**Status:** Approved

## Goal

Split the single `/leet` command into two purpose-built commands:

- `/daily` solves the **official LeetCode daily challenge** (the same problem for
  everyone, every UTC day) and pays the streak-scaled XP multiplier. This is the
  "progress / streak" path.
- `/leet` is the **practice** path: a random problem, optionally scoped to up to
  3 topic tags, that pays reduced-rate XP. This is the "learn a specific topic"
  path.

This decouples the once-per-day multiplier from raw grinding: the big reward is
tied to a problem nobody can cherry-pick, while practice still pays out (at a
reduced, capped rate) so targeted study is rewarded without becoming farmable.

## Decisions (locked during brainstorming)

1. **Streak:** any confirmed solve (daily or practice) keeps the consecutive
   UTC-day streak alive. Only `/daily` cashes the streak in as the multiplier.
2. **Practice XP:** reduced fraction of next-level XP, with a per-day cap. First
   `LEET_PRACTICE_DAILY_CAP` solves/day pay `LEET_PRACTICE_FRACTION x next-level
   XP`; further solves that day pay a small flat `LEET_PRACTICE_OVERFLOW_XP`.
3. **Tags:** up to 3 separate optional args (`tag1`, `tag2`, `tag3`), each
   autocompleted against LeetCode's real topic-tag list and validated server-side.
4. **Daily detection:** the daily counts as solved if the user has an accepted
   submission of today's daily slug dated **anytime today (UTC)**, not only after
   running `/daily`.

## Behavior

### `/daily`

Gating (same as the current `/leet`): `requires_progsuvian`, configured leet
channel set, channel-lock to it, verified LeetCode link, and the one-active-
session-at-a-time guard.

Flow:

1. Block if the user already **completed** today's daily (a completed
   `kind='daily'` assignment with `daily_date == today`): tell them to come back
   tomorrow or use `/leet` to practice.
2. Resolve today's daily problem from the per-process cache (see Daily cache),
   fetching from LeetCode if the cache is stale or empty. If LeetCode is
   unreachable and the cache is empty, fail soft.
3. Read the user's recent accepted submissions. If today's daily slug is already
   accepted **today (UTC)**, award immediately (no thread): create a completed
   `kind='daily'` assignment row for idempotency/stats, then run the solve payout.
4. Otherwise open the private thread (as `/leet` does today), create an active
   `kind='daily'` assignment with `daily_date = today`, and start the existing
   poll/extend session loop. Detection for daily uses the "solved today" rule
   (`find_daily_solve_today`), not the strict post-assignment rule.

Reward: `compute_daily_payout` = the existing streak-scaled `compute_reward_xp`.
Advances the streak, pays once per day (the daily is one problem/day).

### `/leet [tag1] [tag2] [tag3]`

Gating identical to today. Flow mostly unchanged from the current `/leet`:

1. One-active-session guard.
2. Read recent accepted (pre-check) to exclude recently solved slugs.
3. Select a problem:
   - **No tags:** `random_problem(exclude_slugs=...)` from the live pool (current
     behavior, no extra network call).
   - **With tags:** validate each tag against the topic-tag list (reject unknown
     tags with a helpful message), cap at `LEET_MAX_TAGS`, then
     `fetch_questions_by_tags` (LeetCode AND-filters the tags). Drop `paidOnly`
     and excluded slugs, pick randomly. If the filtered set is empty, tell the
     user no free problems match and to drop a tag (do not silently fall back to
     an unrelated random problem).
4. Open thread, create an active `kind='practice'` assignment (`daily_date`
   NULL), start the poll loop with **strict** detection (timestamp strictly after
   assignment, the current `find_matching_submission`).

Reward: `compute_practice_payout(..., practice_solves_today)`. Keeps the streak
alive; pays the capped fraction then the overflow token.

### Shared (unchanged)

Timed reward role grant, public hype line, thread confirmation, `xp_grant` +
`level_change` dispatch, leaderboard/ladder integration, session resume on
restart, grant sweep. The thread confirmation copy branches on assignment kind
and (for practice) whether the cap was hit.

## Pure logic (`core/leetcode.py`)

Replace `compute_solve_payout` with two functions; keep `compute_reward_xp`,
`compute_streak_after_solve`, `find_matching_submission`, and verification helpers
as-is.

```
def compute_daily_payout(last_solve_date, today, current_streak, current_level) -> SolvePayout:
    new_streak = compute_streak_after_solve(last_solve_date, today, current_streak)
    reward = compute_reward_xp(new_streak, current_level)   # streak multiplier
    return SolvePayout(reward_xp=reward, new_streak=new_streak, kind="daily", capped=False)

def compute_practice_payout(last_solve_date, today, current_streak, current_level,
                            practice_solves_today, *, fraction=LEET_PRACTICE_FRACTION,
                            daily_cap=LEET_PRACTICE_DAILY_CAP,
                            overflow_xp=LEET_PRACTICE_OVERFLOW_XP) -> SolvePayout:
    new_streak = compute_streak_after_solve(last_solve_date, today, current_streak)  # keeps streak alive
    if practice_solves_today < daily_cap:
        next_level = min(current_level + 1, LEVEL_CAP)
        reward = max(1, round(fraction * xp_for_level(next_level)))
        capped = False
    else:
        reward = max(0, overflow_xp)
        capped = True
    return SolvePayout(reward_xp=reward, new_streak=new_streak, kind="practice", capped=capped)
```

`SolvePayout` gains `kind: str` and `capped: bool`, and drops `first_of_day`
(its job is now expressed by `kind`/`capped`). `find_daily_solve_today(slug,
today_utc_start_epoch, recent_accepted)` is a new pure detector: returns the
matching submission whose `titleSlug == slug` and `timestamp >= today_start`.

## New pure parsers / data

- `core/leetcode_problems.py`:
  - `parse_daily_challenge(payload) -> (date_str, LeetProblem) | None` from the
    `activeDailyCodingChallengeQuestion` GraphQL `data`.
  - `parse_question_list(payload) -> list[LeetProblem]` from
    `problemsetQuestionList` `data` (skip `paidOnly`, map difficulty strings).
  - A per-process daily cache: `get_cached_daily(today)` / `set_cached_daily(...)`
    keyed by UTC date string; the cog refreshes it on a date change.
- `core/leetcode_tags.py` (new): `TOPIC_TAGS: list[tuple[name, slug]]` (the ~70
  standard LeetCode topic tags), `normalize_tag(value) -> slug | None`, and
  `match_tags(query, limit) -> list[(name, slug)]` for autocomplete.

## Client (`core/leetcode_client.py`)

Two new read-only functions, same hygiene (User-Agent, Referer, timeout,
`LeetCodeUnavailable` on soft failure) as the existing calls:

- `fetch_daily_challenge() -> dict` via the `activeDailyCodingChallengeQuestion`
  GraphQL query (`date`, `link`, `question { titleSlug title difficulty }`).
- `fetch_questions_by_tags(tag_slugs, limit, skip) -> dict` via
  `problemsetQuestionList(categorySlug:"", filters:{tags:[...]})` returning
  `total` and `questions { title titleSlug difficulty paidOnly }`. The cog asks
  for `total` first, then a page from a random `skip`, to randomize selection.

## Schema (one migration)

Add to `leetcode_assignments`:

- `kind` String NOT NULL, `server_default 'practice'` (existing rows become
  practice; they are historical).
- `daily_date` Date NULL, indexed; set only for `kind='daily'`. Enforces one
  daily per UTC day and is unambiguous near the midnight boundary.

## New CRUD (`db/crud.py`)

- `create_assignment(..., kind, daily_date=None)`: add the two params.
- `get_completed_daily_for_date(session, user_id, guild_id, daily_date)`: the
  "already did today's daily" check.
- `count_practice_solves_today(session, user_id, guild_id, today)`: completed
  `kind='practice'` assignments with `completed_at` on `today` (UTC), for the cap.
- `record_leet_solve(session, guild_id, user_id, today, *, kind,
  practice_solves_today=0)`: branch to `compute_daily_payout` /
  `compute_practice_payout`; otherwise identical (lock row, add XP, bump
  `leetcode_solved_total`, set streak + last solve date, recompute level).

`LeetSolveResult` mirrors `SolvePayout`: gains `kind`/`capped`, drops
`first_of_day`.

## Cog (`cogs/leet.py`)

- New `/daily` command per the flow above; new `/leet` tag args + autocomplete.
- `_start_session`/`_run_session`/`_award_solve` carry `kind` so the detector
  picks the right rule and `_award_solve` records the right payout (computing
  `practice_solves_today` inside the solve transaction for race safety).
- Daily cache refresh helper; resume-on-ready passes `kind` through.
- Update module docstring and command descriptions.

## Constants (`core/constants.py`)

- `LEET_PRACTICE_FRACTION: float = 0.25`
- `LEET_PRACTICE_DAILY_CAP: int = 3`
- `LEET_PRACTICE_OVERFLOW_XP: int = 10`
- `LEET_MAX_TAGS: int = 3`
- `LEET_TAG_QUERY_PAGE: int = 50` (page size for the tag query)
- Retire `LEET_EXTRA_SOLVE_XP` (replaced by the practice constants).

## Testing (pure logic only, per repo convention)

- `compute_daily_payout`: streak-scaled, advances streak.
- `compute_practice_payout`: under-cap fraction, over-cap overflow, streak kept
  alive across consecutive days, idempotent same-day streak.
- `find_daily_solve_today`: matches a same-day solve, rejects yesterday's.
- `parse_daily_challenge` / `parse_question_list`: well-formed, malformed,
  `paidOnly` filtering, difficulty mapping.
- `core/leetcode_tags`: `normalize_tag` (case/spacing/aliases), `match_tags`
  autocomplete ordering, unknown-tag rejection.

## Defaults chosen without asking (easy to change)

- Both `/daily` and `/leet` grant the timed reward role and post the hype line.
- One active session at a time total (not one daily + one practice).
- Practice tag filter uses LeetCode AND semantics; a 0-result combo is reported,
  not silently widened.

## Out of scope

- Persisting requested tags on the assignment row (only needed for resume, which
  re-polls without re-rendering the embed).
- Per-user choice of daily difficulty (the daily is fixed by LeetCode).
- Changing the leaderboard, ladder, or reward-role mechanics.
