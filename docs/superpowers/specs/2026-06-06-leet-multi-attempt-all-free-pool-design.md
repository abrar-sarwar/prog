# /leet: multiple attempts per day + all-free problem pool

**Date:** 2026-06-06
**Status:** Approved

## Goal

Encourage more LeetCode practice by letting members run `/leet` as many times
as they want per day (instead of once), while preventing XP farming, and by
drawing problems from **all free (non-premium) LeetCode problems** instead of
the curated 75.

## Behavior changes

### 1. Multiple attempts per UTC day
- Remove the `attempts_today >= 1` gate in `/leet` (`cogs/leet.py`).
- **Keep** the "one active session at a time" guard: a member finishes (or lets
  expire) their current problem before starting another.

### 2. Payout — one full multiplier per day, flat XP after
`record_leet_solve` branches on whether the user already solved today:
- **First solve of the UTC day** → full streak-scaled XP (`compute_reward_xp`)
  and the streak advances. (unchanged)
- **Each extra solve same day** → flat `LEET_EXTRA_SOLVE_XP` (**25**),
  `leetcode_solved_total += 1`, streak unchanged, reward role refreshed.

`LeetSolveResult` gains `first_of_day: bool` so the thread confirmation can read
"full reward, streak now Nd" vs "+25 XP · extra solve · N solved total".

### 3. Problem pool = all free problems (auto-fetch + committed fallback)
- `core/leetcode_client.py`: `fetch_all_free_problems()` → GET
  `https://leetcode.com/api/problems/all/`, keep `paid_only == false`, map
  difficulty `1/2/3 → Easy/Medium/Hard`. (~1,900 problems; includes algorithms,
  database, shell, concurrency — all free.)
- `core/leetcode_problems.py`: a refreshable `ProblemPool` that holds the current
  list, does `random(exclude_slugs)`, and loads a committed snapshot
  `core/data/leetcode_free_problems.json` when the network list is unavailable.
  The legacy LeetCode-75 list stays as a tiny last-resort hardcoded fallback.
- `cogs/leet.py`: load the snapshot on startup (so `/leet` works immediately),
  refresh from the network on ready, and refresh again every
  `LEET_POOL_REFRESH_HOURS` (24) via a `tasks.loop`. A failed refresh keeps the
  existing pool (it never empties).

### 4. Cleanup (daily gate removed)
Delete `/reset-leet-daily` and the helpers that existed only to support the daily
cap: `crud.count_assignments_since`, the daily-reset crud delete helper, and
`_utc_day_start` in the cog. Removes one slash command on next deploy.

## New constants (`core/constants.py`)
- `LEET_EXTRA_SOLVE_XP: int = 25`
- `LEET_POOL_REFRESH_HOURS: int = 24`
- `LEET_ALL_PROBLEMS_URL: str = "https://leetcode.com/api/problems/all/"`

## Testing
- `record_leet_solve`: first solve → full reward + streak++; second same-day →
  flat 25 + solved++ + streak unchanged.
- `ProblemPool`: premium filtering, difficulty mapping, `random(exclude_slugs)`,
  snapshot fallback when the fetch yields nothing.

## Out of scope
- Per-problem dedup beyond the existing recent-AC pre-check.
- Changing streak semantics (still one streak-day per UTC day).
