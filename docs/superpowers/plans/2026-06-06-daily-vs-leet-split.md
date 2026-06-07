# /daily vs /leet Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `/leet` into `/daily` (official LeetCode daily challenge, streak-scaled multiplier) and `/leet` (tag-scoped practice at a reduced, capped rate).

**Architecture:** Pure logic (reward split, parsers, tag list) lives in `core/` with unit tests. Network reads go in `core/leetcode_client.py`. One Alembic migration adds `kind` + `daily_date` to `leetcode_assignments`. All DB access stays funneled through `db/crud.py`; the cog gains `/daily`, tag args + autocomplete on `/leet`, and threads `kind` through the session lifecycle. Changes are additive first (old code paths kept working) with a final cleanup task, so `pytest` passes after every commit.

**Tech Stack:** Python 3.14, discord.py 2.7.1 (`app_commands`), async SQLAlchemy 2.x + asyncpg, Alembic, aiohttp, pytest.

**Spec:** `docs/superpowers/specs/2026-06-06-daily-vs-leet-split-design.md`

**Conventions:** Per the repo, unit tests cover only pure `core/` logic. CRUD, cog, client, and migration changes are verified by `pytest` staying green (import health) plus the manual checklist in Task 12. No em dashes in any code/comments/docs (use hyphens). Commit as solo author; no co-author trailer.

---

### Task 1: Add practice + tag constants

**Files:**
- Modify: `core/constants.py`

- [ ] **Step 1: Add the new constants**

Add these after the existing `LEET_EXTRA_SOLVE_XP` block in `core/constants.py` (keep `LEET_EXTRA_SOLVE_XP` for now; it is removed in Task 11):

```python
# --- /leet practice payout (reduced, capped rate) -------------------------

LEET_PRACTICE_FRACTION: float = 0.25
"""Fraction of the next level's XP cost paid for a practice (/leet) solve, for
the first LEET_PRACTICE_DAILY_CAP solves of the UTC day."""

LEET_PRACTICE_DAILY_CAP: int = 3
"""How many practice solves per UTC day earn the fraction reward. Further solves
that day earn the flat LEET_PRACTICE_OVERFLOW_XP token."""

LEET_PRACTICE_OVERFLOW_XP: int = 10
"""Flat XP for each practice solve beyond the daily cap. Keeps grinding mildly
rewarded without being farmable."""

# --- /leet topic-tag scoping ----------------------------------------------

LEET_MAX_TAGS: int = 3
"""Maximum topic tags a member may pass to /leet (keeps problems scoped but not
targetable to a single problem)."""

LEET_TAG_QUERY_PAGE: int = 50
"""Page size for the tag-filtered problem query against LeetCode."""
```

- [ ] **Step 2: Verify the module imports**

Run: `cd /Users/liam/local/git/prog && python -c "import core.constants"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add core/constants.py
git commit -m "leet: add practice payout + tag-scoping constants"
```

---

### Task 2: Reward split + daily detector (pure logic, TDD)

Adds `compute_daily_payout`, `compute_practice_payout`, and `find_daily_solve_today` alongside the existing `compute_solve_payout` (removed in Task 11). `SolvePayout` gains `kind`/`capped` with defaults so existing callers keep working.

**Files:**
- Modify: `core/leetcode.py`
- Test: `tests/test_leetcode.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_leetcode.py`:

```python
def test_daily_payout_pays_streak_multiplier_and_advances_streak():
    from core.leetcode import compute_daily_payout

    payout = compute_daily_payout(
        last_solve_date=date(2026, 6, 5),
        today=date(2026, 6, 6),
        current_streak=3,
        current_level=5,
    )
    assert payout.kind == "daily"
    assert payout.capped is False
    assert payout.new_streak == 4
    # multiplier reward equals the streak-scaled compute_reward_xp
    from core.leetcode import compute_reward_xp
    assert payout.reward_xp == compute_reward_xp(4, 5)


def test_practice_payout_under_cap_is_fraction_of_next_level():
    from core.leetcode import compute_practice_payout
    from core.constants import LEET_PRACTICE_FRACTION

    payout = compute_practice_payout(
        last_solve_date=date(2026, 6, 6),
        today=date(2026, 6, 6),
        current_streak=2,
        current_level=10,
        practice_solves_today=0,
    )
    assert payout.kind == "practice"
    assert payout.capped is False
    assert payout.reward_xp == max(1, round(LEET_PRACTICE_FRACTION * xp_for_level(11)))


def test_practice_payout_over_cap_pays_flat_overflow():
    from core.leetcode import compute_practice_payout
    from core.constants import LEET_PRACTICE_DAILY_CAP, LEET_PRACTICE_OVERFLOW_XP

    payout = compute_practice_payout(
        last_solve_date=date(2026, 6, 6),
        today=date(2026, 6, 6),
        current_streak=2,
        current_level=10,
        practice_solves_today=LEET_PRACTICE_DAILY_CAP,
    )
    assert payout.capped is True
    assert payout.reward_xp == LEET_PRACTICE_OVERFLOW_XP


def test_practice_payout_keeps_streak_alive_across_days():
    from core.leetcode import compute_practice_payout

    payout = compute_practice_payout(
        last_solve_date=date(2026, 6, 5),
        today=date(2026, 6, 6),
        current_streak=4,
        current_level=1,
        practice_solves_today=0,
    )
    assert payout.new_streak == 5


def test_find_daily_solve_today_accepts_today_rejects_yesterday():
    from core.leetcode import find_daily_solve_today

    today_start = 1_000_000  # arbitrary epoch for "start of today"
    recent = [
        {"titleSlug": "two-sum", "timestamp": "999999"},      # yesterday
        {"titleSlug": "two-sum", "timestamp": "1000050"},     # today
    ]
    hit = find_daily_solve_today("two-sum", today_start, recent)
    assert hit is not None and hit["timestamp"] == "1000050"

    miss = find_daily_solve_today(
        "two-sum", today_start, [{"titleSlug": "two-sum", "timestamp": "999999"}]
    )
    assert miss is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/liam/local/git/prog && pytest tests/test_leetcode.py -k "daily_payout or practice_payout or find_daily_solve_today" -v`
Expected: FAIL / ImportError (the new functions do not exist yet).

- [ ] **Step 3: Extend `SolvePayout` with kind/capped**

In `core/leetcode.py`, replace the `SolvePayout` class with:

```python
class SolvePayout(NamedTuple):
    """The XP + streak outcome of a confirmed solve, before it's persisted."""

    reward_xp: int
    new_streak: int
    first_of_day: bool = True  # legacy; removed in the /daily-vs-/leet cleanup
    kind: str = "practice"     # "daily" | "practice"
    capped: bool = False       # practice solve over the daily cap (token payout)
```

- [ ] **Step 4: Add the new constants to the import block**

In `core/leetcode.py`, update the `from core.constants import (...)` block to also import the practice constants (keep `LEET_EXTRA_SOLVE_XP`):

```python
from core.constants import (
    LEET_EXTRA_SOLVE_XP,
    LEET_PRACTICE_DAILY_CAP,
    LEET_PRACTICE_FRACTION,
    LEET_PRACTICE_OVERFLOW_XP,
    LEET_REWARD_BASE_FRACTION,
    LEET_REWARD_CAP_FRACTION,
    LEET_REWARD_STREAK_MILESTONE_DAYS,
    LEET_VERIFICATION_CODE_CHARS,
    LEET_VERIFICATION_CODE_PREFIX,
)
```

- [ ] **Step 5: Add the three new functions**

In `core/leetcode.py`, add these after `compute_solve_payout` (leave `compute_solve_payout` in place for now):

```python
def compute_daily_payout(
    last_solve_date: date | None,
    today: date,
    current_streak: int,
    current_level: int,
) -> SolvePayout:
    """Payout for an official daily-challenge (/daily) solve.

    Always pays the streak-scaled multiplier (:func:`compute_reward_xp`) and
    advances the streak. The daily is one problem per UTC day, so this is the
    single per-day multiplier.
    """
    new_streak = compute_streak_after_solve(last_solve_date, today, current_streak)
    reward_xp = compute_reward_xp(new_streak, current_level)
    return SolvePayout(
        reward_xp=reward_xp,
        new_streak=new_streak,
        first_of_day=(last_solve_date != today),
        kind="daily",
        capped=False,
    )


def compute_practice_payout(
    last_solve_date: date | None,
    today: date,
    current_streak: int,
    current_level: int,
    practice_solves_today: int,
    *,
    fraction: float = LEET_PRACTICE_FRACTION,
    daily_cap: int = LEET_PRACTICE_DAILY_CAP,
    overflow_xp: int = LEET_PRACTICE_OVERFLOW_XP,
) -> SolvePayout:
    """Payout for a practice (/leet) solve: reduced, capped rate.

    Keeps the streak alive (a practice solve counts as a streak day) but never
    pays the multiplier. The first ``daily_cap`` practice solves of the UTC day
    pay ``fraction`` of the next level's XP cost; further solves pay the flat
    ``overflow_xp`` token. ``practice_solves_today`` is the count of practice
    solves already recorded today, before this one.
    """
    new_streak = compute_streak_after_solve(last_solve_date, today, current_streak)
    if practice_solves_today < daily_cap:
        next_level = min(max(current_level, 0) + 1, LEVEL_CAP)
        reward_xp = max(1, round(fraction * xp_for_level(next_level)))
        capped = False
    else:
        reward_xp = max(0, overflow_xp)
        capped = True
    return SolvePayout(
        reward_xp=reward_xp,
        new_streak=new_streak,
        first_of_day=(last_solve_date != today),
        kind="practice",
        capped=capped,
    )


def find_daily_solve_today(
    daily_slug: str,
    today_start_epoch: int,
    recent_accepted: Iterable[Mapping[str, object]],
) -> Mapping[str, object] | None:
    """Return a recent accepted submission of ``daily_slug`` dated today.

    Unlike :func:`find_matching_submission` (strict: after assignment), the daily
    challenge counts as solved if its slug appears in the recent accepted list
    with a timestamp at/after ``today_start_epoch`` (UTC midnight). Rows with a
    missing/unparseable timestamp are skipped.
    """
    for sub in recent_accepted:
        if sub.get("titleSlug") != daily_slug:
            continue
        raw_ts = sub.get("timestamp")
        try:
            ts = int(raw_ts)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if ts >= today_start_epoch:
            return sub
    return None
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `cd /Users/liam/local/git/prog && pytest tests/test_leetcode.py -k "daily_payout or practice_payout or find_daily_solve_today" -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Run the full leetcode suite (no regressions)**

Run: `cd /Users/liam/local/git/prog && pytest tests/test_leetcode.py -v`
Expected: PASS (all, including the legacy `compute_solve_payout` tests still present).

- [ ] **Step 8: Commit**

```bash
git add core/leetcode.py tests/test_leetcode.py
git commit -m "leet: add daily/practice payout split and daily-solve detector"
```

---

### Task 3: Daily + tag-list parsers and daily cache (pure logic, TDD)

**Files:**
- Modify: `core/leetcode_problems.py`
- Test: `tests/test_leetcode.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_leetcode.py`:

```python
def test_parse_daily_challenge_reads_date_and_problem():
    from core.leetcode_problems import parse_daily_challenge

    data = {
        "activeDailyCodingChallengeQuestion": {
            "date": "2026-06-06",
            "link": "/problems/two-sum/",
            "question": {
                "titleSlug": "two-sum",
                "title": "Two Sum",
                "difficulty": "Easy",
            },
        }
    }
    parsed = parse_daily_challenge(data)
    assert parsed is not None
    date_str, problem = parsed
    assert date_str == "2026-06-06"
    assert problem.slug == "two-sum"
    assert problem.title == "Two Sum"
    assert problem.difficulty == "Easy"


def test_parse_daily_challenge_handles_garbage():
    from core.leetcode_problems import parse_daily_challenge

    assert parse_daily_challenge({}) is None
    assert parse_daily_challenge({"activeDailyCodingChallengeQuestion": None}) is None
    assert parse_daily_challenge(
        {"activeDailyCodingChallengeQuestion": {"date": "x", "question": {}}}
    ) is None


def test_parse_question_list_filters_premium_and_keeps_free():
    from core.leetcode_problems import parse_question_list, parse_question_total

    data = {
        "problemsetQuestionList": {
            "total": 2,
            "questions": [
                {"title": "Two Sum", "titleSlug": "two-sum", "difficulty": "Easy", "paidOnly": False},
                {"title": "Paid", "titleSlug": "paid", "difficulty": "Hard", "paidOnly": True},
            ],
        }
    }
    problems = parse_question_list(data)
    assert [p.slug for p in problems] == ["two-sum"]
    assert parse_question_total(data) == 2


def test_daily_cache_round_trips_by_date():
    from core.leetcode_problems import (
        LeetProblem,
        get_cached_daily,
        set_cached_daily,
    )

    assert get_cached_daily("2099-01-02") is None
    set_cached_daily("2099-01-01", LeetProblem("two-sum", "Two Sum", "Easy"))
    assert get_cached_daily("2099-01-01").slug == "two-sum"
    assert get_cached_daily("2099-01-02") is None  # stale date misses
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/liam/local/git/prog && pytest tests/test_leetcode.py -k "parse_daily or parse_question or daily_cache" -v`
Expected: FAIL / ImportError.

- [ ] **Step 3: Implement the parsers and cache**

In `core/leetcode_problems.py`, add at the end of the module:

```python
def parse_daily_challenge(
    data: Mapping[str, object],
) -> tuple[str, LeetProblem] | None:
    """Parse an ``activeDailyCodingChallengeQuestion`` GraphQL ``data`` object.

    Returns ``(date_str, LeetProblem)`` or None for a malformed payload.
    """
    node = (
        data.get("activeDailyCodingChallengeQuestion")
        if isinstance(data, Mapping)
        else None
    )
    if not isinstance(node, Mapping):
        return None
    date_str = node.get("date")
    question = node.get("question")
    question = question if isinstance(question, Mapping) else {}
    slug = question.get("titleSlug")
    title = question.get("title")
    if (
        not isinstance(date_str, str)
        or not isinstance(slug, str)
        or not isinstance(title, str)
        or not slug
    ):
        return None
    difficulty = question.get("difficulty")
    difficulty = difficulty if isinstance(difficulty, str) and difficulty else "Unknown"
    return date_str, LeetProblem(slug, title, difficulty)


def parse_question_list(data: Mapping[str, object]) -> list[LeetProblem]:
    """Parse a ``problemsetQuestionList`` GraphQL ``data`` object into free
    problems (drops ``paidOnly`` rows; difficulty is already Easy/Medium/Hard)."""
    node = (
        data.get("problemsetQuestionList") if isinstance(data, Mapping) else None
    )
    if not isinstance(node, Mapping):
        return []
    questions = node.get("questions")
    if not isinstance(questions, Sequence):
        return []
    out: list[LeetProblem] = []
    for q in questions:
        if not isinstance(q, Mapping) or q.get("paidOnly"):
            continue
        slug = q.get("titleSlug")
        title = q.get("title")
        if not isinstance(slug, str) or not isinstance(title, str) or not slug:
            continue
        difficulty = q.get("difficulty")
        difficulty = (
            difficulty if isinstance(difficulty, str) and difficulty else "Unknown"
        )
        out.append(LeetProblem(slug, title, difficulty))
    return out


def parse_question_total(data: Mapping[str, object]) -> int:
    """Return the ``total`` count from a ``problemsetQuestionList`` payload."""
    node = (
        data.get("problemsetQuestionList") if isinstance(data, Mapping) else None
    )
    if not isinstance(node, Mapping):
        return 0
    total = node.get("total")
    return int(total) if isinstance(total, int) else 0


# Per-process daily-challenge cache, keyed by the LeetCode-reported date string.
_daily_cache: tuple[str, LeetProblem] | None = None


def get_cached_daily(today_str: str) -> LeetProblem | None:
    """Return the cached daily problem if it matches ``today_str``, else None."""
    if _daily_cache is not None and _daily_cache[0] == today_str:
        return _daily_cache[1]
    return None


def set_cached_daily(date_str: str, problem: LeetProblem) -> None:
    """Cache the daily problem for ``date_str`` (replaces any prior entry)."""
    global _daily_cache
    _daily_cache = (date_str, problem)
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `cd /Users/liam/local/git/prog && pytest tests/test_leetcode.py -k "parse_daily or parse_question or daily_cache" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add core/leetcode_problems.py tests/test_leetcode.py
git commit -m "leet: add daily + tag-list parsers and per-process daily cache"
```

---

### Task 4: Topic-tag list + validation/autocomplete helpers (pure logic, TDD)

**Files:**
- Create: `core/leetcode_tags.py`
- Test: `tests/test_leetcode_tags.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leetcode_tags.py`:

```python
"""Unit tests for the LeetCode topic-tag list and helpers."""

from __future__ import annotations

from core.leetcode_tags import TOPIC_TAGS, match_tags, normalize_tag


def test_topic_tags_are_unique_slugs():
    slugs = [slug for _, slug in TOPIC_TAGS]
    assert len(slugs) == len(set(slugs))


def test_normalize_accepts_slug_name_and_spacing():
    assert normalize_tag("dynamic-programming") == "dynamic-programming"
    assert normalize_tag("Dynamic Programming") == "dynamic-programming"
    assert normalize_tag("  dynamic programming ") == "dynamic-programming"
    assert normalize_tag("ARRAY") == "array"


def test_normalize_rejects_unknown():
    assert normalize_tag("not-a-real-tag") is None
    assert normalize_tag("") is None


def test_match_prefers_prefix_then_substring():
    results = match_tags("dyn", limit=5)
    assert ("Dynamic Programming", "dynamic-programming") in results
    # all returned entries are real tags
    assert all(t in TOPIC_TAGS for t in results)


def test_match_empty_query_returns_capped_list():
    results = match_tags("", limit=10)
    assert len(results) == 10
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/liam/local/git/prog && pytest tests/test_leetcode_tags.py -v`
Expected: FAIL / ModuleNotFoundError.

- [ ] **Step 3: Implement the tag module**

Create `core/leetcode_tags.py`:

```python
"""LeetCode topic tags: the canonical (name, slug) list plus helpers for
/leet's optional tag arguments.

Pure data + functions so /leet's autocomplete and validation can be unit-tested
without Discord. The slugs match LeetCode's GraphQL ``topicTags.slug`` values,
which the tag-filtered problem query expects.
"""

from __future__ import annotations

# (display name, LeetCode slug). Ordered roughly by how common the topic is so a
# bare autocomplete (empty query) surfaces the useful ones first.
TOPIC_TAGS: list[tuple[str, str]] = [
    ("Array", "array"),
    ("String", "string"),
    ("Hash Table", "hash-table"),
    ("Dynamic Programming", "dynamic-programming"),
    ("Math", "math"),
    ("Sorting", "sorting"),
    ("Greedy", "greedy"),
    ("Depth-First Search", "depth-first-search"),
    ("Binary Search", "binary-search"),
    ("Breadth-First Search", "breadth-first-search"),
    ("Tree", "tree"),
    ("Matrix", "matrix"),
    ("Two Pointers", "two-pointers"),
    ("Bit Manipulation", "bit-manipulation"),
    ("Binary Tree", "binary-tree"),
    ("Heap (Priority Queue)", "heap-priority-queue"),
    ("Stack", "stack"),
    ("Graph", "graph"),
    ("Prefix Sum", "prefix-sum"),
    ("Simulation", "simulation"),
    ("Design", "design"),
    ("Counting", "counting"),
    ("Backtracking", "backtracking"),
    ("Sliding Window", "sliding-window"),
    ("Union Find", "union-find"),
    ("Linked List", "linked-list"),
    ("Ordered Set", "ordered-set"),
    ("Monotonic Stack", "monotonic-stack"),
    ("Enumeration", "enumeration"),
    ("Recursion", "recursion"),
    ("Trie", "trie"),
    ("Divide and Conquer", "divide-and-conquer"),
    ("Bitmask", "bitmask"),
    ("Queue", "queue"),
    ("Number Theory", "number-theory"),
    ("Binary Indexed Tree", "binary-indexed-tree"),
    ("Segment Tree", "segment-tree"),
    ("Geometry", "geometry"),
    ("Memoization", "memoization"),
    ("Hash Function", "hash-function"),
    ("Combinatorics", "combinatorics"),
    ("Topological Sort", "topological-sort"),
    ("String Matching", "string-matching"),
    ("Shortest Path", "shortest-path"),
    ("Rolling Hash", "rolling-hash"),
    ("Game Theory", "game-theory"),
    ("Interactive", "interactive"),
    ("Data Stream", "data-stream"),
    ("Monotonic Queue", "monotonic-queue"),
    ("Brainteaser", "brainteaser"),
    ("Doubly-Linked List", "doubly-linked-list"),
    ("Randomized", "randomized"),
    ("Merge Sort", "merge-sort"),
    ("Counting Sort", "counting-sort"),
    ("Iterator", "iterator"),
    ("Concurrency", "concurrency"),
    ("Probability and Statistics", "probability-and-statistics"),
    ("Quickselect", "quickselect"),
    ("Suffix Array", "suffix-array"),
    ("Bucket Sort", "bucket-sort"),
    ("Minimum Spanning Tree", "minimum-spanning-tree"),
    ("Shell", "shell"),
    ("Line Sweep", "line-sweep"),
    ("Reservoir Sampling", "reservoir-sampling"),
    ("Strongly Connected Component", "strongly-connected-component"),
    ("Eulerian Circuit", "eulerian-circuit"),
    ("Radix Sort", "radix-sort"),
    ("Rejection Sampling", "rejection-sampling"),
    ("Biconnected Component", "biconnected-component"),
]

_SLUGS: frozenset[str] = frozenset(slug for _, slug in TOPIC_TAGS)
_BY_NAME: dict[str, str] = {name.casefold(): slug for name, slug in TOPIC_TAGS}


def normalize_tag(value: str) -> str | None:
    """Return the canonical slug for ``value`` (slug, display name, or spaced),
    or None if it is not a known LeetCode topic tag."""
    if not value:
        return None
    v = value.strip().casefold()
    if v in _SLUGS:
        return v
    if v in _BY_NAME:
        return _BY_NAME[v]
    hyphenated = v.replace(" ", "-")
    if hyphenated in _SLUGS:
        return hyphenated
    return None


def match_tags(query: str, limit: int = 25) -> list[tuple[str, str]]:
    """Return up to ``limit`` (name, slug) tags matching ``query`` for
    autocomplete: prefix matches first, then substring matches. An empty query
    returns the head of the list."""
    q = (query or "").strip().casefold()
    if not q:
        return TOPIC_TAGS[:limit]
    prefix = [
        t for t in TOPIC_TAGS if t[0].casefold().startswith(q) or t[1].startswith(q)
    ]
    seen = set(prefix)
    substring = [
        t
        for t in TOPIC_TAGS
        if t not in seen and (q in t[0].casefold() or q in t[1])
    ]
    return (prefix + substring)[:limit]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/liam/local/git/prog && pytest tests/test_leetcode_tags.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add core/leetcode_tags.py tests/test_leetcode_tags.py
git commit -m "leet: add topic-tag list with normalize + autocomplete helpers"
```

---

### Task 5: LeetCode client reads for daily + tag-filtered problems

No unit tests (network I/O, per repo convention). Verified by import health.

**Files:**
- Modify: `core/leetcode_client.py`

- [ ] **Step 1: Add the GraphQL query constants**

In `core/leetcode_client.py`, after the existing `_RECENT_AC_QUERY` definition, add:

```python
_DAILY_CHALLENGE_QUERY = """
query progDailyChallenge {
  activeDailyCodingChallengeQuestion {
    date
    link
    question {
      titleSlug
      title
      difficulty
    }
  }
}
"""

_QUESTION_LIST_QUERY = """
query progProblemList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
  problemsetQuestionList: questionList(
    categorySlug: $categorySlug
    limit: $limit
    skip: $skip
    filters: $filters
  ) {
    total: totalNum
    questions: data {
      title
      titleSlug
      difficulty
      paidOnly
    }
  }
}
"""

_PROBLEMSET_REFERER = "https://leetcode.com/problemset/all/"
```

- [ ] **Step 2: Add the two fetch functions**

In `core/leetcode_client.py`, after `fetch_recent_accepted`, add:

```python
async def fetch_daily_challenge() -> dict:
    """Return the GraphQL ``data`` for LeetCode's active daily challenge.

    Caller parses it via :func:`core.leetcode_problems.parse_daily_challenge`.
    Raises :class:`LeetCodeUnavailable` on a soft failure.
    """
    return await _graphql(_DAILY_CHALLENGE_QUERY, {}, _PROBLEMSET_REFERER)


async def fetch_questions_by_tags(
    tag_slugs: list[str], limit: int, skip: int = 0
) -> dict:
    """Return the GraphQL ``data`` for problems carrying all of ``tag_slugs``.

    LeetCode AND-filters the tags. The result includes ``total`` (for random
    paging) and a page of ``questions`` (each with ``paidOnly``); caller parses
    via :func:`core.leetcode_problems.parse_question_list` /
    :func:`parse_question_total`. Raises :class:`LeetCodeUnavailable` on a soft
    failure.
    """
    variables = {
        "categorySlug": "",
        "limit": limit,
        "skip": skip,
        "filters": {"tags": list(tag_slugs)},
    }
    return await _graphql(_QUESTION_LIST_QUERY, variables, _PROBLEMSET_REFERER)
```

- [ ] **Step 3: Verify import health and no test regressions**

Run: `cd /Users/liam/local/git/prog && python -c "import core.leetcode_client" && pytest -q`
Expected: import OK; pytest passes (all tests so far).

- [ ] **Step 4: Commit**

```bash
git add core/leetcode_client.py
git commit -m "leet: add client reads for daily challenge + tag-filtered problems"
```

---

### Task 6: Schema migration for assignment kind + daily_date

**Files:**
- Modify: `db/models.py`
- Create: `migrations/versions/<generated>_leet_assignment_kind_daily_date.py`

- [ ] **Step 1: Add the model columns**

In `db/models.py`, inside `class LeetcodeAssignment`, add after the `status` column:

```python
    kind: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'practice'")
    )
    """'daily' (official daily challenge, multiplier payout) or 'practice'
    (/leet, reduced+capped payout). Existing rows default to 'practice'."""
    daily_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    """For kind='daily' only: the UTC date of the daily challenge. Enforces one
    daily per UTC day and is unambiguous across the midnight boundary."""
```

(`date`, `Date`, `String`, and `text` are already imported in this module.)

- [ ] **Step 2: Generate the migration stub (auto-wires down_revision to head)**

Run: `cd /Users/liam/local/git/prog && ENV_FILE=.env.dev alembic revision -m "leet assignment kind and daily_date"`
Expected: prints `Generating .../migrations/versions/<hash>_leet_assignment_kind_and_daily_date.py ... done`. Note the created path.

If `.env.dev` is not configured, use `.env`. The plain `alembic revision` (no `--autogenerate`) does not connect to the DB; it only needs the script directory.

- [ ] **Step 3: Fill in upgrade/downgrade**

Edit the generated file so its body reads (keep the auto-generated `revision`/`down_revision` lines and the `import sqlalchemy as sa` / `from alembic import op` header):

```python
def upgrade() -> None:
    op.add_column(
        "leetcode_assignments",
        sa.Column(
            "kind", sa.String(), nullable=False, server_default="practice"
        ),
    )
    op.add_column(
        "leetcode_assignments",
        sa.Column("daily_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_leetcode_assignments_daily_date",
        "leetcode_assignments",
        ["daily_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_leetcode_assignments_daily_date",
        table_name="leetcode_assignments",
    )
    op.drop_column("leetcode_assignments", "daily_date")
    op.drop_column("leetcode_assignments", "kind")
```

- [ ] **Step 4: Confirm the migration is the new single head**

Run: `cd /Users/liam/local/git/prog && alembic heads`
Expected: exactly one head, the new revision (its `down_revision` should be `0a4cac82120a`).

- [ ] **Step 5: Apply it to the dev database**

Run: `cd /Users/liam/local/git/prog && ENV_FILE=.env.dev alembic upgrade head`
Expected: `Running upgrade 0a4cac82120a -> <hash>, leet assignment kind and daily_date`.

- [ ] **Step 6: Verify model imports and tests still pass**

Run: `cd /Users/liam/local/git/prog && python -c "import db.models" && pytest -q`
Expected: import OK; pytest passes.

- [ ] **Step 7: Commit**

```bash
git add db/models.py migrations/versions/
git commit -m "leet: schema for assignment kind + daily_date"
```

---

### Task 7: CRUD for kind-aware solves + daily/practice queries

No unit tests (DB layer, per repo convention). Verified by import health + suite staying green. `record_leet_solve` keeps `first_of_day` on its result and defaults `kind='practice'` so the existing cog call keeps working until Task 11.

**Files:**
- Modify: `db/crud.py`

- [ ] **Step 1: Update imports**

In `db/crud.py`, change the leetcode import and the datetime import:

Replace:
```python
from core.leetcode import compute_solve_payout
```
with:
```python
from core.leetcode import compute_daily_payout, compute_practice_payout
```

Replace:
```python
from datetime import date, datetime
```
with:
```python
from datetime import date, datetime, timedelta, timezone
```

- [ ] **Step 2: Add `kind` + `daily_date` params to `create_assignment`**

In `db/crud.py`, update `create_assignment`'s signature and body. Add the two keyword params and pass them to the model:

```python
async def create_assignment(
    session: AsyncSession,
    *,
    discord_user_id: int,
    guild_id: int,
    problem_slug: str,
    problem_title: str,
    difficulty: str,
    assigned_at: datetime,
    expires_at: datetime,
    thread_id: int | None = None,
    kind: str = "practice",
    daily_date: date | None = None,
) -> LeetcodeAssignment:
    """Insert a new active assignment row and return it."""
    assignment = LeetcodeAssignment(
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        problem_slug=problem_slug,
        problem_title=problem_title,
        difficulty=difficulty,
        assigned_at=assigned_at,
        expires_at=expires_at,
        thread_id=thread_id,
        status="active",
        kind=kind,
        daily_date=daily_date,
    )
    session.add(assignment)
    await session.flush()  # populate assignment.id for the caller
    return assignment
```

- [ ] **Step 3: Add the daily + practice-count query helpers**

In `db/crud.py`, add after `get_active_assignment`:

```python
async def get_completed_daily_for_date(
    session: AsyncSession, discord_user_id: int, guild_id: int, daily_date: date
) -> LeetcodeAssignment | None:
    """Return the user's completed daily-challenge assignment for ``daily_date``
    in this guild, or None. Used to block a second /daily on the same UTC day."""
    stmt = select(LeetcodeAssignment).where(
        LeetcodeAssignment.discord_user_id == discord_user_id,
        LeetcodeAssignment.guild_id == guild_id,
        LeetcodeAssignment.kind == "daily",
        LeetcodeAssignment.daily_date == daily_date,
        LeetcodeAssignment.status == "completed",
    )
    return (await session.execute(stmt)).scalars().first()


async def count_practice_solves_today(
    session: AsyncSession, discord_user_id: int, guild_id: int, today: date
) -> int:
    """Count completed practice (/leet) solves for this user/guild on ``today``
    (UTC). Drives the practice daily cap in :func:`compute_practice_payout`."""
    start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    stmt = select(func.count(LeetcodeAssignment.id)).where(
        LeetcodeAssignment.discord_user_id == discord_user_id,
        LeetcodeAssignment.guild_id == guild_id,
        LeetcodeAssignment.kind == "practice",
        LeetcodeAssignment.status == "completed",
        LeetcodeAssignment.completed_at >= start,
        LeetcodeAssignment.completed_at < end,
    )
    return int((await session.execute(stmt)).scalar_one())
```

- [ ] **Step 4: Make `LeetSolveResult` and `record_leet_solve` kind-aware**

In `db/crud.py`, replace the `LeetSolveResult` class and `record_leet_solve` function with:

```python
class LeetSolveResult(NamedTuple):
    """Outcome of recording a confirmed solve."""

    change: LevelChange
    reward_xp: int
    new_streak: int
    kind: str
    capped: bool
    first_of_day: bool  # legacy; removed in the /daily-vs-/leet cleanup


async def record_leet_solve(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    today: date,
    *,
    kind: str = "practice",
    practice_solves_today: int = 0,
) -> LeetSolveResult:
    """Apply a confirmed solve (XP + streak + total), atomically.

    Locks the user row and derives the payout by kind:
    ``daily`` pays the streak-scaled multiplier (:func:`compute_daily_payout`);
    ``practice`` pays the reduced, capped rate (:func:`compute_practice_payout`,
    using ``practice_solves_today``). Either way the solved total is bumped, the
    streak is kept alive, XP flows through the same ``xp``/level pipeline as
    messages and voice, and the level is recomputed.
    """
    user = await _lock_user(session, guild_id, user_id)
    old_level = user.level
    if kind == "daily":
        payout = compute_daily_payout(
            user.leetcode_last_solve_date, today, user.leetcode_streak, old_level
        )
    else:
        payout = compute_practice_payout(
            user.leetcode_last_solve_date,
            today,
            user.leetcode_streak,
            old_level,
            practice_solves_today,
        )
    user.xp = user.xp + payout.reward_xp
    user.leetcode_solved_total = user.leetcode_solved_total + 1
    user.leetcode_streak = payout.new_streak
    user.leetcode_last_solve_date = today
    user.level = level_from_total_xp(user.xp)
    return LeetSolveResult(
        change=LevelChange(user=user, old_level=old_level, new_level=user.level),
        reward_xp=payout.reward_xp,
        new_streak=payout.new_streak,
        kind=payout.kind,
        capped=payout.capped,
        first_of_day=payout.first_of_day,
    )
```

- [ ] **Step 5: Verify import health + tests green**

Run: `cd /Users/liam/local/git/prog && python -c "import db.crud" && pytest -q`
Expected: import OK; pytest passes.

- [ ] **Step 6: Commit**

```bash
git add db/crud.py
git commit -m "leet: kind-aware record_leet_solve + daily/practice-count queries"
```

---

### Task 8: Extract shared gate + thread helpers in the cog

Pure refactor of the existing `/leet` to create reusable helpers that `/daily` will also use. Behavior unchanged.

**Files:**
- Modify: `cogs/leet.py`

- [ ] **Step 1: Add the two helper methods**

In `cogs/leet.py`, inside `class Leet`, add these helpers just above the `# /leet` section comment:

```python
    async def _check_leet_preconditions(
        self, interaction: discord.Interaction
    ) -> tuple[int, "crud.LeetcodeLink"] | None:
        """Shared /leet + /daily gate. Returns (leet_channel_id, link) or None.

        On failure it sends the ephemeral reply and returns None. Caller must
        have already deferred the interaction. Channel-presence, channel-lock,
        and verification are enforced here; the progsuvian gate is the command
        decorator.
        """
        assert interaction.guild is not None
        member = interaction.user
        async with get_session_factory()() as session:
            config = await crud.get_or_create_guild_config(
                session, interaction.guild.id
            )
            leet_channel_id = config.leetcode_channel_id
            link = await crud.get_leetcode_link(session, member.id)
            await session.commit()
        if leet_channel_id is None:
            await interaction.followup.send(
                "leetcode channel isn't set. an admin needs to run `/setup-leet` first.",
                ephemeral=True,
            )
            return None
        if interaction.channel_id != leet_channel_id:
            await interaction.followup.send(
                f"this only works in <#{leet_channel_id}>.", ephemeral=True
            )
            return None
        if link is None:
            await interaction.followup.send(
                "link your leetcode first with `/leetverify <username>`.",
                ephemeral=True,
            )
            return None
        return leet_channel_id, link

    async def _open_session_thread(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        leet_channel_id: int,
        member: discord.Member,
        problem,
        label: str,
    ) -> discord.Thread | None:
        """Open the private thread for a session. Returns the thread or None
        (after sending the ephemeral error). ``label`` prefixes the thread name
        (e.g. 'leet' or 'daily')."""
        channel = guild.get_channel(leet_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "the configured leetcode channel is missing or not a text channel. "
                "an admin should re-run `/setup-leet`.",
                ephemeral=True,
            )
            return None
        try:
            thread = await channel.create_thread(
                name=f"{label} · {member.display_name} · {problem.title}"[:100],
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=1440,
            )
            await thread.add_user(member)
        except discord.Forbidden:
            await interaction.followup.send(
                "i can't create a private thread here. an admin needs to give me "
                "**Create Private Threads** (and **Send Messages in Threads**) in "
                "this channel.",
                ephemeral=True,
            )
            return None
        except discord.HTTPException as exc:
            log.warning(
                "leet: thread creation failed in guild %s: %s", guild.id, exc
            )
            await interaction.followup.send(
                "discord wouldn't let me open your thread, try again in a bit.",
                ephemeral=True,
            )
            return None
        return thread
```

- [ ] **Step 2: Rewrite `/leet` to use the helpers (behavior unchanged)**

In `cogs/leet.py`, replace the body of the `leet` command from its `await interaction.response.defer(...)` line through the thread-creation block (down to and including the `expires_at = now + ...` assignment) so it uses the helpers. The new body up to assignment creation:

```python
        await interaction.response.defer(ephemeral=True, thinking=True)

        now = datetime.now(timezone.utc)
        pre = await self._check_leet_preconditions(interaction)
        if pre is None:
            return
        leet_channel_id, link = pre

        # One active session at a time (daily or practice).
        async with get_session_factory()() as session:
            active = await crud.get_active_assignment(session, member.id, guild.id)
            await session.commit()
        if active is not None:
            where = f" <#{active.thread_id}>" if active.thread_id else ""
            await interaction.followup.send(
                f"you've already got an active session{where}. finish that one first.",
                ephemeral=True,
            )
            return

        # Pre-check: avoid handing out a problem they recently solved.
        try:
            recent = await fetch_recent_accepted(
                link.leetcode_username, LEET_RECENT_AC_LIMIT
            )
        except LeetCodeUnavailable as exc:
            log.warning("leet: recent-AC pre-check failed for %s: %s", member.id, exc)
            await interaction.followup.send(
                "couldn't reach leetcode to set up your problem, try again in a bit.",
                ephemeral=True,
            )
            return
        solved_slugs = {r.get("titleSlug") for r in recent if r.get("titleSlug")}
        problem = random_problem(exclude_slugs=solved_slugs) or random_problem()
        if problem is None:
            await interaction.followup.send(
                "the problem pool is empty, tell an admin.", ephemeral=True
            )
            return

        thread = await self._open_session_thread(
            interaction, guild, leet_channel_id, member, problem, "leet"
        )
        if thread is None:
            return

        expires_at = now + timedelta(minutes=LEET_SESSION_MINUTES)
```

Leave the assignment-creation block, `_post_problem`, followup, and `_start_session` call that follow it as they are for now (Task 11 adds `kind` to them).

- [ ] **Step 3: Verify import health + tests green**

Run: `cd /Users/liam/local/git/prog && python -c "import cogs.leet" && pytest -q`
Expected: import OK; pytest passes.

- [ ] **Step 4: Commit**

```bash
git add cogs/leet.py
git commit -m "leet: extract shared precondition + thread helpers"
```

---

### Task 9: Tag args + autocomplete on /leet

**Files:**
- Modify: `cogs/leet.py`

- [ ] **Step 1: Add imports for tags + tag-query helpers**

In `cogs/leet.py`, add to the imports:

```python
from core import leetcode_tags
from core.constants import (  # add to the existing core.constants import block
    LEET_MAX_TAGS,
    LEET_TAG_QUERY_PAGE,
)
from core.leetcode_client import fetch_questions_by_tags  # add to the existing import
from core.leetcode_problems import (  # add to the existing import block
    parse_question_list,
    parse_question_total,
)
```

(Merge these into the existing import statements rather than duplicating module imports.)

- [ ] **Step 2: Add the module-level autocomplete callback**

In `cogs/leet.py`, add near the top (after `log = logging.getLogger(__name__)`):

```python
async def _tag_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete /leet tag args against the real LeetCode topic list."""
    return [
        app_commands.Choice(name=name, value=slug)
        for name, slug in leetcode_tags.match_tags(current, 25)
    ]
```

- [ ] **Step 3: Add the tag-picker helper**

In `cogs/leet.py`, inside `class Leet`, add after `_open_session_thread`:

```python
    async def _pick_tagged_problem(
        self, tag_slugs: list[str], exclude_slugs: set
    ):
        """Pick a random free problem carrying all ``tag_slugs``.

        Queries LeetCode for the filtered total, then a random page, and picks a
        free problem not in ``exclude_slugs``. Returns a LeetProblem, or None if
        nothing matches / LeetCode is unavailable.
        """
        try:
            head = await fetch_questions_by_tags(tag_slugs, limit=1, skip=0)
        except LeetCodeUnavailable as exc:
            log.warning("leet: tag query (head) failed for %s: %s", tag_slugs, exc)
            return None
        total = parse_question_total(head)
        if total <= 0:
            return None
        skip = random.randint(0, max(0, total - 1))
        start = min(skip, max(0, total - LEET_TAG_QUERY_PAGE))
        try:
            page = await fetch_questions_by_tags(
                tag_slugs, limit=LEET_TAG_QUERY_PAGE, skip=start
            )
        except LeetCodeUnavailable as exc:
            log.warning("leet: tag query (page) failed for %s: %s", tag_slugs, exc)
            return None
        problems = parse_question_list(page)
        candidates = [p for p in problems if p.slug not in exclude_slugs]
        if not candidates:
            candidates = problems  # everything recently solved; allow a repeat
        if not candidates:
            return None
        return random.choice(candidates)
```

- [ ] **Step 4: Add tag args + autocomplete to the `/leet` command and wire selection**

In `cogs/leet.py`, update the `leet` command decorators and signature:

```python
    @app_commands.command(
        name="leet",
        description="Practice: a random LeetCode problem, optionally scoped to topics",
    )
    @app_commands.describe(
        tag1="Optional topic to scope the problem",
        tag2="Optional second topic",
        tag3="Optional third topic",
    )
    @app_commands.autocomplete(
        tag1=_tag_autocomplete, tag2=_tag_autocomplete, tag3=_tag_autocomplete
    )
    @app_commands.guild_only()
    @requires_progsuvian()
    async def leet(
        self,
        interaction: discord.Interaction,
        tag1: str | None = None,
        tag2: str | None = None,
        tag3: str | None = None,
    ) -> None:
```

Then, in the `/leet` body, replace the problem-selection line
`problem = random_problem(exclude_slugs=solved_slugs) or random_problem()` with:

```python
        # Resolve requested tags (slug from autocomplete, or typed name/slug).
        requested = [t for t in (tag1, tag2, tag3) if t]
        tag_slugs: list[str] = []
        for raw in requested:
            slug = leetcode_tags.normalize_tag(raw)
            if slug is None:
                await interaction.followup.send(
                    f"`{raw}` isn't a known leetcode topic. start typing to pick "
                    "one from the list.",
                    ephemeral=True,
                )
                return
            if slug not in tag_slugs:
                tag_slugs.append(slug)
        tag_slugs = tag_slugs[:LEET_MAX_TAGS]

        if tag_slugs:
            problem = await self._pick_tagged_problem(tag_slugs, solved_slugs)
            if problem is None:
                pretty = ", ".join(f"`{s}`" for s in tag_slugs)
                await interaction.followup.send(
                    f"no free problems match {pretty} together. try dropping a tag.",
                    ephemeral=True,
                )
                return
        else:
            problem = random_problem(exclude_slugs=solved_slugs) or random_problem()
        if problem is None:
            await interaction.followup.send(
                "the problem pool is empty, tell an admin.", ephemeral=True
            )
            return
```

- [ ] **Step 5: Verify import health + tests green**

Run: `cd /Users/liam/local/git/prog && python -c "import cogs.leet" && pytest -q`
Expected: import OK; pytest passes.

- [ ] **Step 6: Commit**

```bash
git add cogs/leet.py
git commit -m "leet: optional topic-tag scoping with autocomplete on /leet"
```

---

### Task 10: Add the /daily command

**Files:**
- Modify: `cogs/leet.py`

- [ ] **Step 1: Add imports for the daily flow**

In `cogs/leet.py`, merge into the existing imports:

```python
from core.leetcode import find_daily_solve_today  # add to the core.leetcode import
from core.leetcode_client import fetch_daily_challenge  # add to the client import
from core.leetcode_problems import (  # add to the existing import block
    get_cached_daily,
    parse_daily_challenge,
    set_cached_daily,
)
```

- [ ] **Step 2: Add the `/daily` command**

In `cogs/leet.py`, add this command method inside `class Leet`, right after the `leet` command and its `_post_problem` helper:

```python
    @app_commands.command(
        name="daily",
        description="Solve today's official LeetCode daily challenge for the streak multiplier",
    )
    @app_commands.guild_only()
    @requires_progsuvian()
    async def daily(self, interaction: discord.Interaction) -> None:
        """Run today's official daily challenge: the streak-multiplier path."""
        assert interaction.guild is not None
        guild = interaction.guild
        member = interaction.user
        assert isinstance(member, discord.Member)
        await interaction.response.defer(ephemeral=True, thinking=True)

        now = datetime.now(timezone.utc)
        today = now.date()
        pre = await self._check_leet_preconditions(interaction)
        if pre is None:
            return
        leet_channel_id, link = pre

        # Already did today's daily? One active session at a time otherwise.
        async with get_session_factory()() as session:
            done = await crud.get_completed_daily_for_date(
                session, member.id, guild.id, today
            )
            active = await crud.get_active_assignment(session, member.id, guild.id)
            await session.commit()
        if done is not None:
            await interaction.followup.send(
                "you've already done today's daily. come back tomorrow, or run "
                "`/leet` to practice a topic.",
                ephemeral=True,
            )
            return
        if active is not None:
            where = f" <#{active.thread_id}>" if active.thread_id else ""
            await interaction.followup.send(
                f"you've already got an active session{where}. finish that one first.",
                ephemeral=True,
            )
            return

        # Resolve today's daily problem (cache, else fetch).
        problem = get_cached_daily(today.isoformat())
        if problem is None:
            try:
                data = await fetch_daily_challenge()
            except LeetCodeUnavailable as exc:
                log.warning("daily: fetch failed: %s", exc)
                await interaction.followup.send(
                    "couldn't reach leetcode for today's daily, try again in a bit.",
                    ephemeral=True,
                )
                return
            parsed = parse_daily_challenge(data)
            if parsed is None:
                await interaction.followup.send(
                    "couldn't read today's daily from leetcode, try again in a bit.",
                    ephemeral=True,
                )
                return
            date_str, problem = parsed
            set_cached_daily(date_str, problem)

        # Already solved today on leetcode (anytime today UTC)? Instant award.
        today_start_epoch = int(
            datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp()
        )
        try:
            recent = await fetch_recent_accepted(
                link.leetcode_username, LEET_RECENT_AC_LIMIT
            )
        except LeetCodeUnavailable as exc:
            log.warning("daily: recent-AC check failed for %s: %s", member.id, exc)
            recent = []
        if recent and find_daily_solve_today(problem.slug, today_start_epoch, recent):
            async with get_session_factory()() as session:
                assignment = await crud.create_assignment(
                    session,
                    discord_user_id=member.id,
                    guild_id=guild.id,
                    problem_slug=problem.slug,
                    problem_title=problem.title,
                    difficulty=problem.difficulty,
                    assigned_at=now,
                    expires_at=now,
                    thread_id=None,
                    kind="daily",
                    daily_date=today,
                )
                assignment_id = assignment.id
                await session.commit()
            await self._award_solve(member, assignment_id, 0, "daily")
            await interaction.followup.send(
                f"nice, you already solved today's daily (**{problem.title}**). "
                "counted it.",
                ephemeral=True,
            )
            return

        # Otherwise open a thread + start the detection session.
        thread = await self._open_session_thread(
            interaction, guild, leet_channel_id, member, problem, "daily"
        )
        if thread is None:
            return

        expires_at = now + timedelta(minutes=LEET_SESSION_MINUTES)
        async with get_session_factory()() as session:
            assignment = await crud.create_assignment(
                session,
                discord_user_id=member.id,
                guild_id=guild.id,
                problem_slug=problem.slug,
                problem_title=problem.title,
                difficulty=problem.difficulty,
                assigned_at=now,
                expires_at=expires_at,
                thread_id=thread.id,
                kind="daily",
                daily_date=today,
            )
            assignment_id = assignment.id
            await session.commit()

        await self._post_problem(thread, member, problem, expires_at)
        await interaction.followup.send(
            f"today's daily is in {thread.mention}. good luck.", ephemeral=True
        )
        self._start_session(
            member,
            assignment_id,
            thread.id,
            problem.slug,
            link.leetcode_username,
            now,
            expires_at,
            "daily",
        )
```

Note: `_award_solve` and `_start_session` gain their `kind` parameter in Task 11; this command already passes it, and Task 11 lands before any run.

- [ ] **Step 3: Verify import health**

Run: `cd /Users/liam/local/git/prog && python -c "import cogs.leet"`
Expected: import OK. (Tests still pass; the `kind` arg on `_start_session`/`_award_solve` is added in Task 11. If you run between tasks, do Task 11 before invoking the cog.)

- [ ] **Step 4: Commit**

```bash
git add cogs/leet.py
git commit -m "leet: add /daily official daily-challenge command"
```

---

### Task 11: Thread `kind` through the session lifecycle + confirmation copy

Makes `_start_session`, `_run_session`, `_award_solve`, and resume carry `kind`, picks the right detector, computes the practice count, and updates the thread confirmation. Then removes the now-dead legacy code paths.

**Files:**
- Modify: `cogs/leet.py`
- Modify: `core/leetcode.py`
- Modify: `core/constants.py`
- Modify: `db/crud.py`
- Modify: `tests/test_leetcode.py`

- [ ] **Step 1: Add `kind` to the `/leet` assignment creation + session start**

In `cogs/leet.py`, in the `/leet` command, update the assignment creation call to pass `kind="practice"` and the `_start_session` call to pass `"practice"`:

```python
        async with get_session_factory()() as session:
            assignment = await crud.create_assignment(
                session,
                discord_user_id=member.id,
                guild_id=guild.id,
                problem_slug=problem.slug,
                problem_title=problem.title,
                difficulty=problem.difficulty,
                assigned_at=now,
                expires_at=expires_at,
                thread_id=thread.id,
                kind="practice",
                daily_date=None,
            )
            assignment_id = assignment.id
            await session.commit()

        await self._post_problem(thread, member, problem, expires_at)
        await interaction.followup.send(
            f"your problem's in {thread.mention}. good luck.", ephemeral=True
        )
        self._start_session(
            member, assignment_id, thread.id, problem.slug,
            link.leetcode_username, now, expires_at, "practice",
        )
```

- [ ] **Step 2: Add `kind` to `_start_session` and `_run_session`**

In `cogs/leet.py`, update `_start_session` to accept and forward `kind`:

```python
    def _start_session(
        self,
        member: discord.Member,
        assignment_id: int,
        thread_id: int,
        slug: str,
        username: str,
        assigned_at: datetime,
        expires_at: datetime,
        kind: str,
    ) -> None:
        """Spawn (and track) the detection task for one assignment."""
        task = asyncio.create_task(
            self._run_session(
                member, assignment_id, thread_id, slug, username,
                assigned_at, expires_at, kind,
            )
        )
        self._sessions[assignment_id] = task
```

Then update `_run_session`'s signature to add `kind: str` (last param) and its detection block. Replace the detection `try` block inside the loop with:

```python
                try:
                    recent = await fetch_recent_accepted(
                        username, LEET_RECENT_AC_LIMIT
                    )
                    if kind == "daily":
                        day = now.date()
                        today_start = int(
                            datetime(
                                day.year, day.month, day.day, tzinfo=timezone.utc
                            ).timestamp()
                        )
                        hit = find_daily_solve_today(slug, today_start, recent)
                    else:
                        hit = find_matching_submission(slug, assigned_epoch, recent)
                    if hit:
                        await self._award_solve(member, assignment_id, thread_id, kind)
                        return
                except LeetCodeUnavailable as exc:
                    log.warning(
                        "leet poll: transient failure for assignment %s: %s",
                        assignment_id,
                        exc,
                    )
```

- [ ] **Step 3: Make `_award_solve` kind-aware**

In `cogs/leet.py`, update `_award_solve` signature and the solve-recording block:

```python
    async def _award_solve(
        self, member: discord.Member, assignment_id: int, thread_id: int, kind: str
    ) -> None:
```

and replace the `async with get_session_factory()() as session:` block's contents up to `await session.commit()` with:

```python
        async with get_session_factory()() as session:
            if not await crud.complete_assignment(session, assignment_id, now):
                await session.commit()
                return
            practice_solves_today = 0
            if kind == "practice":
                # This solve is already counted (just completed); subtract it to
                # get the number solved BEFORE it, which drives the cap.
                counted = await crud.count_practice_solves_today(
                    session, member.id, guild.id, today
                )
                practice_solves_today = max(0, counted - 1)
            result = await crud.record_leet_solve(
                session,
                guild.id,
                member.id,
                today,
                kind=kind,
                practice_solves_today=practice_solves_today,
            )
            change = result.change
            aura_already_fired = change.user.aura_message_fired
            if (
                change.leveled_up
                and change.new_level >= LEVEL_CAP
                and not aura_already_fired
            ):
                change.user.aura_message_fired = True
            config = await crud.get_or_create_guild_config(session, guild.id)
            reward_role_id = config.leetcode_reward_role_id
            leet_channel_id = config.leetcode_channel_id
            await session.commit()
```

- [ ] **Step 3b: Update the `approve-leet` admin call site**

In `cogs/leet.py`, the `approve_leet` command calls `_award_solve` with three args; it has the assignment in `active`, so pass its kind. Replace:

```python
        await self._award_solve(user, active.id, active.thread_id or 0)
```
with:
```python
        await self._award_solve(user, active.id, active.thread_id or 0, active.kind)
```

- [ ] **Step 4: Update the thread confirmation copy to use kind/capped**

In `cogs/leet.py`, replace the `_post_solve_confirmation` body's `if result.first_of_day:` / `else:` block with:

```python
        change = result.change
        if result.kind == "daily":
            lines = [
                f"daily done, that's **+{result.reward_xp:,} xp**",
                f"streak: **{result.new_streak}** day(s), total solved: "
                f"**{change.user.leetcode_solved_total}**",
            ]
        elif result.capped:
            lines = [
                f"accepted - past today's practice bonus, **+{result.reward_xp:,} xp**",
                f"streak: **{result.new_streak}** day(s), total solved: "
                f"**{change.user.leetcode_solved_total}**",
            ]
        else:
            lines = [
                f"accepted, practice solve **+{result.reward_xp:,} xp**",
                f"streak: **{result.new_streak}** day(s), total solved: "
                f"**{change.user.leetcode_solved_total}**",
            ]
```

- [ ] **Step 5: Update the resume-on-ready path to pass `kind`**

In `cogs/leet.py`, in `on_ready`, update the `_start_session` call in the resume loop to forward the assignment's kind:

```python
            self._start_session(
                member, a.id, a.thread_id, a.problem_slug,
                link_username, a.assigned_at, a.expires_at, a.kind,
            )
```

- [ ] **Step 6: Remove legacy `compute_solve_payout` + `first_of_day`**

Now that nothing reads them:

In `core/leetcode.py`, delete the `compute_solve_payout` function and remove `first_of_day` from `SolvePayout` (and the `first_of_day=...` kwargs in `compute_daily_payout` / `compute_practice_payout`). Remove `LEET_EXTRA_SOLVE_XP` from its import block. Final `SolvePayout`:

```python
class SolvePayout(NamedTuple):
    """The XP + streak outcome of a confirmed solve, before it's persisted."""

    reward_xp: int
    new_streak: int
    kind: str       # "daily" | "practice"
    capped: bool    # practice solve over the daily cap (token payout)
```

In `db/crud.py`, remove `first_of_day` from `LeetSolveResult` and from the `record_leet_solve` return.

In `core/constants.py`, delete `LEET_EXTRA_SOLVE_XP` and its docstring.

In `tests/test_leetcode.py`, delete the three legacy tests that call `compute_solve_payout` (`test_payout_first_solve_of_day_pays_full_reward`, `test_payout_continues_streak_when_yesterday`, `test_payout_extra_solve_same_day_is_flat_and_keeps_streak`) and remove `LEET_EXTRA_SOLVE_XP` and `compute_solve_payout` from the test imports.

- [ ] **Step 7: Verify import health + full suite green**

Run: `cd /Users/liam/local/git/prog && python -c "import cogs.leet" && pytest -q`
Expected: import OK; pytest passes (legacy payout tests gone, new ones present).

- [ ] **Step 8: Grep for stragglers**

Run: `cd /Users/liam/local/git/prog && grep -rn "compute_solve_payout\|first_of_day\|LEET_EXTRA_SOLVE_XP" core db cogs tests`
Expected: no matches.

- [ ] **Step 9: Commit**

```bash
git add core/leetcode.py core/constants.py db/crud.py cogs/leet.py tests/test_leetcode.py
git commit -m "leet: thread kind through sessions, kind-aware payout, drop legacy path"
```

---

### Task 12: Docs + manual verification

**Files:**
- Modify: `README.md`
- Modify: `cogs/leet.py` (module docstring only)

- [ ] **Step 1: Update the cog module docstring**

In `cogs/leet.py`, update the top-of-file docstring to describe both commands: `/daily` (official daily challenge, streak multiplier, counts a same-day solve instantly) and `/leet` (tag-scoped practice at the reduced+capped rate), plus `/leetverify` and the admin commands. Keep it factual and short.

- [ ] **Step 2: Update the README**

In `README.md`, in the user-commands table and the "/leet (LeetCode website-solve feature)" section, document:
- `/daily` - official LeetCode daily challenge, pays the streak-scaled multiplier once per UTC day, counts a solve done anytime today.
- `/leet [tag1] [tag2] [tag3]` - practice; reduced, capped XP (first `LEET_PRACTICE_DAILY_CAP`/day at `LEET_PRACTICE_FRACTION` of next level, then `LEET_PRACTICE_OVERFLOW_XP`); optional up to `LEET_MAX_TAGS` autocompleted topic tags (AND-filtered).
- Streak: any confirmed solve (daily or practice) keeps the streak; only `/daily` pays the multiplier.

- [ ] **Step 3: Manual smoke test against the dev guild**

Start the bot (`ENV_FILE=.env.dev python main.py` if using the isolated DB, else `python main.py`) and in the configured leet channel verify:
- `/daily` opens a thread with today's LeetCode daily; solving it on leetcode.com gets detected and pays the multiplier; streak advances.
- Running `/daily` again the same day is blocked ("already done today's daily").
- If you solved today's daily before running `/daily`, it awards instantly with no thread.
- `/leet` with no tags behaves as before but pays the reduced practice rate; after `LEET_PRACTICE_DAILY_CAP` solves the payout drops to the overflow token.
- `/leet tag1:dynamic-programming tag2:array` autocompletes and yields a DP+array problem; an impossible combo replies "no free problems match ... drop a tag".
- A practice solve on a fresh day keeps the streak alive.

- [ ] **Step 4: Commit**

```bash
git add README.md cogs/leet.py
git commit -m "leet: document /daily vs /leet split"
```

---

## Notes for the implementer

- **Detection rules differ by kind:** daily uses "solved anytime today (UTC)" (`find_daily_solve_today`); practice uses "after assignment" (`find_matching_submission`). Do not unify them.
- **Practice cap counting:** `count_practice_solves_today` is queried after `complete_assignment` marks the current solve completed, so subtract 1 to get prior solves (Task 11, Step 3).
- **One active session total** (daily or practice) is intentional; both commands check `get_active_assignment`.
- **Tag filter is AND:** LeetCode requires a problem to carry all listed tags. A 0-result combo is reported to the user, never silently widened.
- **Green per commit:** legacy `compute_solve_payout`/`first_of_day`/`LEET_EXTRA_SOLVE_XP` are kept until Task 11 specifically so the suite passes after each commit.
