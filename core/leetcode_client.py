"""Read-only async client for LeetCode's public GraphQL endpoint.

Used for two things only:

* :func:`fetch_profile`         - read a user's public profile (for /leetverify;
  the ``about_me`` bio is where the verification code is checked).
* :func:`fetch_recent_accepted` - read a user's recent accepted submissions
  (for solve detection).

GraphQL hygiene (per spec): every request sends a browser-like User-Agent and a
Referer pointing at the user's profile, uses public data only (no login, token,
or cookies), and has a hard timeout. Any network/HTTP/GraphQL failure is raised
as :class:`LeetCodeUnavailable` so callers can fail soft with a clear message
and never crash the cog. A genuinely-absent user resolves to ``None`` (profile)
or ``[]`` (submissions), distinct from an error.

LeetCode does not expose a stable numeric internal user id on the public API
(``matchedUser.id`` is null; ``userSlug`` tracks the username), so the canonical
username returned here is the strongest available identity handle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from core.constants import (
    LEET_ALL_PROBLEMS_URL,
    LEET_HTTP_TIMEOUT_SECONDS,
    LEET_USER_AGENT,
)

log = logging.getLogger(__name__)

_GRAPHQL_URL = "https://leetcode.com/graphql"

_PROFILE_QUERY = """
query progUserProfile($username: String!) {
  matchedUser(username: $username) {
    username
    profile {
      realName
      aboutMe
      userAvatar
      ranking
      userSlug
    }
  }
}
"""

_RECENT_AC_QUERY = """
query progRecentAc($username: String!, $limit: Int!) {
  recentAcSubmissionList(username: $username, limit: $limit) {
    id
    title
    titleSlug
    timestamp
  }
}
"""

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
      isPaidOnly
    }
  }
}
"""

_PROBLEMSET_REFERER = "https://leetcode.com/problemset/all/"


class LeetCodeError(Exception):
    """Base class for LeetCode client errors."""


class LeetCodeUnavailable(LeetCodeError):
    """LeetCode could not be reached or returned an error/garbage response.

    Callers should treat this as a soft failure (tell the user to try again),
    never as "the user/submission doesn't exist".
    """


class LeetCodeUserNotFound(LeetCodeError):
    """LeetCode reported that the queried username does not exist.

    Distinct from :class:`LeetCodeUnavailable`: this is a definitive "no such
    user", surfaced by LeetCode as a GraphQL error rather than a null result.
    """


@dataclass(frozen=True)
class LeetProfile:
    """A user's public profile, as far as we read it."""

    username: str
    """LeetCode's canonical casing of the username (what we store + display)."""
    user_slug: str | None
    about_me: str | None
    """The bio/About field — where /leetverify looks for the code."""
    real_name: str | None
    avatar: str | None
    ranking: int | None


def _profile_url(username: str) -> str:
    """The public profile URL, used as the Referer header."""
    return f"https://leetcode.com/u/{username}/"


async def _graphql(
    query: str, variables: dict[str, object], referer: str
) -> dict:
    """POST a GraphQL query and return the ``data`` object.

    Raises :class:`LeetCodeUnavailable` on any transport, HTTP, or GraphQL-level
    error (including a ``errors`` array in the response body).
    """
    headers = {
        "Content-Type": "application/json",
        "User-Agent": LEET_USER_AGENT,
        "Referer": referer,
        "Accept": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=LEET_HTTP_TIMEOUT_SECONDS)
    payload = {"query": query, "variables": variables}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                _GRAPHQL_URL, json=payload, headers=headers
            ) as resp:
                if resp.status != 200:
                    text = (await resp.text())[:200]
                    raise LeetCodeUnavailable(
                        f"LeetCode returned HTTP {resp.status}: {text}"
                    )
                body = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        raise LeetCodeUnavailable(f"network error talking to LeetCode: {exc}") from exc
    except (TimeoutError, __import__("asyncio").TimeoutError) as exc:
        raise LeetCodeUnavailable("timed out talking to LeetCode") from exc

    if not isinstance(body, dict):
        raise LeetCodeUnavailable("LeetCode returned a non-object response")
    errors = body.get("errors")
    if errors:
        # A missing user is reported as a GraphQL error, not a null result;
        # treat that as a definitive "not found" rather than a soft failure.
        messages = " ".join(
            str(e.get("message", "")) for e in errors if isinstance(e, dict)
        ).casefold()
        if "does not exist" in messages or "no such user" in messages:
            raise LeetCodeUserNotFound(messages)
        raise LeetCodeUnavailable(f"LeetCode GraphQL errors: {errors}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise LeetCodeUnavailable("LeetCode response had no data object")
    return data


async def fetch_profile(username: str) -> LeetProfile | None:
    """Return the public profile for ``username``, or None if no such user.

    Raises :class:`LeetCodeUnavailable` on a soft failure (network/HTTP/GraphQL).
    """
    try:
        data = await _graphql(
            _PROFILE_QUERY, {"username": username}, _profile_url(username)
        )
    except LeetCodeUserNotFound:
        return None
    matched = data.get("matchedUser")
    if not matched:
        return None  # no such user
    profile = matched.get("profile") or {}
    ranking = profile.get("ranking")
    return LeetProfile(
        username=matched.get("username") or username,
        user_slug=profile.get("userSlug"),
        about_me=profile.get("aboutMe"),
        real_name=profile.get("realName"),
        avatar=profile.get("userAvatar"),
        ranking=int(ranking) if isinstance(ranking, (int, float)) else None,
    )


async def fetch_recent_accepted(
    username: str, limit: int
) -> list[dict]:
    """Return up to ``limit`` recent accepted submissions for ``username``.

    Each row is ``{"id","title","titleSlug","timestamp"}`` (timestamp is a unix
    seconds string, as LeetCode returns it). Empty list if the user has none.
    Raises :class:`LeetCodeUnavailable` on a soft failure. A username that no
    longer exists resolves to an empty list (the user was real at link time).
    """
    try:
        data = await _graphql(
            _RECENT_AC_QUERY,
            {"username": username, "limit": limit},
            _profile_url(username),
        )
    except LeetCodeUserNotFound:
        log.warning("recent-AC lookup: user %r no longer exists on LeetCode", username)
        return []
    rows = data.get("recentAcSubmissionList")
    if not isinstance(rows, list):
        return []
    return rows


async def fetch_daily_challenge() -> dict:
    """Return the GraphQL ``data`` for LeetCode's active daily challenge.

    Caller parses it via :func:`core.leetcode_problems.parse_daily_challenge`.
    Raises :class:`LeetCodeUnavailable` on a soft failure.
    """
    return await _graphql(_DAILY_CHALLENGE_QUERY, {}, _PROBLEMSET_REFERER)


async def fetch_filtered_questions(
    tag_slugs: list[str],
    difficulty: str | None,
    limit: int,
    skip: int = 0,
) -> dict:
    """Return the GraphQL ``data`` for problems matching the given filters.

    Any combination of ``tag_slugs`` (AND-filtered) and ``difficulty`` (one of
    ``"EASY"``/``"MEDIUM"``/``"HARD"``) is applied; pass an empty list / None to
    omit that filter. The result includes ``total`` (for random paging) and a
    page of ``questions`` (each with ``isPaidOnly``); caller parses via
    :func:`core.leetcode_problems.parse_question_list` /
    :func:`parse_question_total`. Raises :class:`LeetCodeUnavailable` on a soft
    failure.
    """
    filters: dict[str, object] = {}
    if tag_slugs:
        filters["tags"] = list(tag_slugs)
    if difficulty:
        filters["difficulty"] = difficulty
    variables = {
        "categorySlug": "",
        "limit": limit,
        "skip": skip,
        "filters": filters,
    }
    return await _graphql(_QUESTION_LIST_QUERY, variables, _PROBLEMSET_REFERER)


async def fetch_all_problems_payload() -> dict:
    """GET LeetCode's public problem list and return the raw JSON object.

    This is the ``/api/problems/all/`` REST endpoint (not GraphQL); its payload
    carries a ``stat_status_pairs`` array with each problem's slug, title,
    difficulty level, and ``paid_only`` flag. Parsing/filtering into the free
    pool lives in :func:`core.leetcode_problems.parse_problem_list` so it stays
    pure and unit-testable. The payload is ~1-2 MB, so a more generous timeout
    than the GraphQL calls is used. Raises :class:`LeetCodeUnavailable` on any
    transport/HTTP error.
    """
    headers = {
        "User-Agent": LEET_USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://leetcode.com/problemset/all/",
    }
    timeout = aiohttp.ClientTimeout(total=LEET_HTTP_TIMEOUT_SECONDS * 3)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(LEET_ALL_PROBLEMS_URL, headers=headers) as resp:
                if resp.status != 200:
                    text = (await resp.text())[:200]
                    raise LeetCodeUnavailable(
                        f"problem list returned HTTP {resp.status}: {text}"
                    )
                body = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        raise LeetCodeUnavailable(
            f"network error fetching problem list: {exc}"
        ) from exc
    except (TimeoutError, __import__("asyncio").TimeoutError) as exc:
        raise LeetCodeUnavailable("timed out fetching problem list") from exc
    if not isinstance(body, dict):
        raise LeetCodeUnavailable("problem list response was not a JSON object")
    return body
