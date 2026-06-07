"""The problem pool for the /leet feature — all free (non-premium) problems.

The pool is loaded at runtime from LeetCode's public problem list
(``/api/problems/all/``, fetched by :mod:`core.leetcode_client`) and refreshed
periodically by the cog. It is layered so /leet never goes dark:

1. **Live list** — every free problem on LeetCode (~3k), refreshed on a timer.
2. **Committed snapshot** — ``core/data/leetcode_free_problems.json``, generated
   from the same endpoint and committed to the repo. Loaded at import so /leet
   works immediately on boot and survives any network outage.
3. **Inline seed** — a small hardcoded set of classic free problems, the
   absolute last resort if the snapshot file is missing or unreadable.

Parsing (:func:`parse_problem_list`) and selection (:class:`ProblemPool`) are
pure and unit-tested; the network fetch lives in the client and the refresh
wiring lives in the cog.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

log = logging.getLogger(__name__)

_SNAPSHOT_PATH = Path(__file__).parent / "data" / "leetcode_free_problems.json"

# LeetCode's ``/api/problems/all/`` encodes difficulty as a numeric level.
_DIFFICULTY_BY_LEVEL: dict[int, str] = {1: "Easy", 2: "Medium", 3: "Hard"}


@dataclass(frozen=True)
class LeetProblem:
    """One problem in the pool. ``difficulty`` is one of Easy/Medium/Hard."""

    slug: str
    title: str
    difficulty: str

    @property
    def url(self) -> str:
        """The canonical problem URL on LeetCode."""
        return f"https://leetcode.com/problems/{self.slug}/"


# Absolute last-resort seed: a handful of well-known free problems. Only used if
# the committed snapshot can't be read. The snapshot is the real fallback.
_SEED: list[LeetProblem] = [
    LeetProblem("two-sum", "Two Sum", "Easy"),
    LeetProblem("valid-parentheses", "Valid Parentheses", "Easy"),
    LeetProblem("merge-two-sorted-lists", "Merge Two Sorted Lists", "Easy"),
    LeetProblem("best-time-to-buy-and-sell-stock", "Best Time to Buy and Sell Stock", "Easy"),
    LeetProblem("valid-palindrome", "Valid Palindrome", "Easy"),
    LeetProblem("invert-binary-tree", "Invert Binary Tree", "Easy"),
    LeetProblem("longest-substring-without-repeating-characters", "Longest Substring Without Repeating Characters", "Medium"),
    LeetProblem("3sum", "3Sum", "Medium"),
    LeetProblem("product-of-array-except-self", "Product of Array Except Self", "Medium"),
    LeetProblem("group-anagrams", "Group Anagrams", "Medium"),
    LeetProblem("number-of-islands", "Number of Islands", "Medium"),
    LeetProblem("coin-change", "Coin Change", "Medium"),
]


def parse_problem_list(payload: Mapping[str, object]) -> list[LeetProblem]:
    """Turn an ``/api/problems/all/`` payload into the free-problem list.

    Keeps only rows with ``paid_only`` falsey and a usable slug+title; maps the
    numeric difficulty level to Easy/Medium/Hard (unknown levels -> "Unknown").
    Returns an empty list for a malformed payload so callers can fall back.
    """
    pairs = payload.get("stat_status_pairs") if isinstance(payload, Mapping) else None
    if not isinstance(pairs, Sequence):
        return []
    out: list[LeetProblem] = []
    for item in pairs:
        if not isinstance(item, Mapping) or item.get("paid_only"):
            continue
        stat = item.get("stat")
        stat = stat if isinstance(stat, Mapping) else {}
        slug = stat.get("question__title_slug")
        title = stat.get("question__title")
        if not isinstance(slug, str) or not isinstance(title, str) or not slug:
            continue
        diff = item.get("difficulty")
        level = diff.get("level") if isinstance(diff, Mapping) else None
        out.append(
            LeetProblem(slug, title, _DIFFICULTY_BY_LEVEL.get(level, "Unknown"))
        )
    return out


class ProblemPool:
    """A mutable, hot-swappable set of problems with random selection.

    Held as a module singleton (:data:`_pool`); the cog calls :meth:`replace`
    on each successful refresh. :meth:`replace` ignores an empty list so a bad
    refresh can never empty the pool.
    """

    def __init__(self, problems: Iterable[LeetProblem]) -> None:
        self._problems: list[LeetProblem] = list(problems)
        self._by_slug: dict[str, LeetProblem] = {p.slug: p for p in self._problems}

    def __len__(self) -> int:
        return len(self._problems)

    def replace(self, problems: Iterable[LeetProblem]) -> bool:
        """Swap in a new problem list. No-op (returns False) if it's empty."""
        new = list(problems)
        if not new:
            return False
        self._problems = new
        self._by_slug = {p.slug: p for p in new}
        return True

    def get(self, slug: str) -> LeetProblem | None:
        """Return the problem with ``slug``, or None if not in the pool."""
        return self._by_slug.get(slug)

    def random(
        self,
        rng: random.Random | None = None,
        *,
        exclude_slugs: frozenset[str] | set[str] | None = None,
    ) -> LeetProblem | None:
        """Return a random problem, optionally excluding some slugs.

        Returns None only if every problem is excluded (or the pool is empty).
        """
        picker = rng or random
        pool = self._problems
        if exclude_slugs:
            pool = [p for p in pool if p.slug not in exclude_slugs]
        if not pool:
            return None
        return picker.choice(pool)


def _load_snapshot() -> list[LeetProblem]:
    """Read the committed snapshot, or fall back to the inline seed."""
    try:
        raw = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        problems = [
            LeetProblem(r["slug"], r["title"], r.get("difficulty", "Unknown"))
            for r in raw
            if isinstance(r, Mapping) and r.get("slug") and r.get("title")
        ]
        if problems:
            return problems
        log.warning("leet pool: snapshot was empty; using inline seed")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("leet pool: snapshot load failed (%s); using inline seed", exc)
    return list(_SEED)


# Module singleton. Loaded from the snapshot at import so /leet works at once;
# the cog refreshes it from the network shortly after startup and on a timer.
_pool = ProblemPool(_load_snapshot())


def random_problem(
    rng: random.Random | None = None,
    *,
    exclude_slugs: frozenset[str] | set[str] | None = None,
) -> LeetProblem | None:
    """Return a random problem from the live pool (see :meth:`ProblemPool.random`)."""
    return _pool.random(rng, exclude_slugs=exclude_slugs)


def get_problem(slug: str) -> LeetProblem | None:
    """Return the pooled problem with ``slug``, or None."""
    return _pool.get(slug)


def pool_size() -> int:
    """Current number of problems in the live pool."""
    return len(_pool)


def replace_pool(problems: Iterable[LeetProblem]) -> bool:
    """Swap the live pool's contents (no-op on empty). Returns True if replaced."""
    return _pool.replace(problems)


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
        or not date_str
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
    problems (drops ``isPaidOnly`` rows; difficulty is already Easy/Medium/Hard)."""
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
        if not isinstance(q, Mapping) or q.get("isPaidOnly"):
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


# per-process daily-challenge cache, keyed by the LeetCode-reported date string
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
