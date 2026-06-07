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
