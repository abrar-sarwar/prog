"""The curated LeetCode problem pool for the /leet feature (Blind 75).

This is the editable seed list. Every entry below was verified against the
live ``leetcode.com`` GraphQL endpoint at build time — the ``slug`` resolves to
a real problem and the ``difficulty``/``title`` are LeetCode's own values, not
guesses. The six paid-only Blind 75 problems (encode-and-decode-strings,
alien-dictionary, graph-valid-tree, number-of-connected-components-in-an-
undirected-graph, meeting-rooms, meeting-rooms-ii) are intentionally excluded
so that free LeetCode accounts can always solve the assigned problem.

To curate the pool, edit :data:`PROBLEM_POOL` directly — add/remove rows. Keep
slugs accurate (the slug is the ``leetcode.com/problems/<slug>/`` path segment);
the cog links to and detects solves by slug.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


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


# Blind 75 (free problems only), verified live. Order is by category for
# readability; selection is random so order has no functional meaning.
PROBLEM_POOL: list[LeetProblem] = [
    # Arrays & Hashing
    LeetProblem("two-sum", "Two Sum", "Easy"),
    LeetProblem("best-time-to-buy-and-sell-stock", "Best Time to Buy and Sell Stock", "Easy"),
    LeetProblem("contains-duplicate", "Contains Duplicate", "Easy"),
    LeetProblem("product-of-array-except-self", "Product of Array Except Self", "Medium"),
    LeetProblem("maximum-subarray", "Maximum Subarray", "Medium"),
    LeetProblem("maximum-product-subarray", "Maximum Product Subarray", "Medium"),
    LeetProblem("find-minimum-in-rotated-sorted-array", "Find Minimum in Rotated Sorted Array", "Medium"),
    LeetProblem("search-in-rotated-sorted-array", "Search in Rotated Sorted Array", "Medium"),
    LeetProblem("3sum", "3Sum", "Medium"),
    LeetProblem("container-with-most-water", "Container With Most Water", "Medium"),
    LeetProblem("group-anagrams", "Group Anagrams", "Medium"),
    LeetProblem("valid-anagram", "Valid Anagram", "Easy"),
    LeetProblem("top-k-frequent-elements", "Top K Frequent Elements", "Medium"),
    # Binary
    LeetProblem("sum-of-two-integers", "Sum of Two Integers", "Medium"),
    LeetProblem("number-of-1-bits", "Number of 1 Bits", "Easy"),
    LeetProblem("counting-bits", "Counting Bits", "Easy"),
    LeetProblem("missing-number", "Missing Number", "Easy"),
    LeetProblem("reverse-bits", "Reverse Bits", "Easy"),
    # Dynamic Programming
    LeetProblem("climbing-stairs", "Climbing Stairs", "Easy"),
    LeetProblem("coin-change", "Coin Change", "Medium"),
    LeetProblem("longest-increasing-subsequence", "Longest Increasing Subsequence", "Medium"),
    LeetProblem("longest-common-subsequence", "Longest Common Subsequence", "Medium"),
    LeetProblem("word-break", "Word Break", "Medium"),
    LeetProblem("combination-sum", "Combination Sum", "Medium"),
    LeetProblem("house-robber", "House Robber", "Medium"),
    LeetProblem("house-robber-ii", "House Robber II", "Medium"),
    LeetProblem("decode-ways", "Decode Ways", "Medium"),
    LeetProblem("unique-paths", "Unique Paths", "Medium"),
    LeetProblem("jump-game", "Jump Game", "Medium"),
    # Graph
    LeetProblem("clone-graph", "Clone Graph", "Medium"),
    LeetProblem("course-schedule", "Course Schedule", "Medium"),
    LeetProblem("pacific-atlantic-water-flow", "Pacific Atlantic Water Flow", "Medium"),
    LeetProblem("number-of-islands", "Number of Islands", "Medium"),
    LeetProblem("longest-consecutive-sequence", "Longest Consecutive Sequence", "Medium"),
    # Intervals
    LeetProblem("insert-interval", "Insert Interval", "Medium"),
    LeetProblem("merge-intervals", "Merge Intervals", "Medium"),
    LeetProblem("non-overlapping-intervals", "Non-overlapping Intervals", "Medium"),
    # Linked List
    LeetProblem("reverse-linked-list", "Reverse Linked List", "Easy"),
    LeetProblem("linked-list-cycle", "Linked List Cycle", "Easy"),
    LeetProblem("merge-two-sorted-lists", "Merge Two Sorted Lists", "Easy"),
    LeetProblem("merge-k-sorted-lists", "Merge k Sorted Lists", "Hard"),
    LeetProblem("remove-nth-node-from-end-of-list", "Remove Nth Node From End of List", "Medium"),
    LeetProblem("reorder-list", "Reorder List", "Medium"),
    # Matrix
    LeetProblem("set-matrix-zeroes", "Set Matrix Zeroes", "Medium"),
    LeetProblem("spiral-matrix", "Spiral Matrix", "Medium"),
    LeetProblem("rotate-image", "Rotate Image", "Medium"),
    LeetProblem("word-search", "Word Search", "Medium"),
    # Strings
    LeetProblem("longest-substring-without-repeating-characters", "Longest Substring Without Repeating Characters", "Medium"),
    LeetProblem("longest-repeating-character-replacement", "Longest Repeating Character Replacement", "Medium"),
    LeetProblem("minimum-window-substring", "Minimum Window Substring", "Hard"),
    LeetProblem("valid-parentheses", "Valid Parentheses", "Easy"),
    LeetProblem("valid-palindrome", "Valid Palindrome", "Easy"),
    LeetProblem("longest-palindromic-substring", "Longest Palindromic Substring", "Medium"),
    LeetProblem("palindromic-substrings", "Palindromic Substrings", "Medium"),
    # Trees
    LeetProblem("maximum-depth-of-binary-tree", "Maximum Depth of Binary Tree", "Easy"),
    LeetProblem("same-tree", "Same Tree", "Easy"),
    LeetProblem("invert-binary-tree", "Invert Binary Tree", "Easy"),
    LeetProblem("binary-tree-maximum-path-sum", "Binary Tree Maximum Path Sum", "Hard"),
    LeetProblem("binary-tree-level-order-traversal", "Binary Tree Level Order Traversal", "Medium"),
    LeetProblem("serialize-and-deserialize-binary-tree", "Serialize and Deserialize Binary Tree", "Hard"),
    LeetProblem("subtree-of-another-tree", "Subtree of Another Tree", "Easy"),
    LeetProblem("construct-binary-tree-from-preorder-and-inorder-traversal", "Construct Binary Tree from Preorder and Inorder Traversal", "Medium"),
    LeetProblem("validate-binary-search-tree", "Validate Binary Search Tree", "Medium"),
    LeetProblem("kth-smallest-element-in-a-bst", "Kth Smallest Element in a BST", "Medium"),
    LeetProblem("lowest-common-ancestor-of-a-binary-search-tree", "Lowest Common Ancestor of a Binary Search Tree", "Medium"),
    LeetProblem("implement-trie-prefix-tree", "Implement Trie (Prefix Tree)", "Medium"),
    LeetProblem("design-add-and-search-words-data-structure", "Design Add and Search Words Data Structure", "Medium"),
    LeetProblem("word-search-ii", "Word Search II", "Hard"),
    # Heap
    LeetProblem("find-median-from-data-stream", "Find Median from Data Stream", "Hard"),
]

_BY_SLUG: dict[str, LeetProblem] = {p.slug: p for p in PROBLEM_POOL}


def get_problem(slug: str) -> LeetProblem | None:
    """Return the pool problem with ``slug``, or None if it's not in the pool."""
    return _BY_SLUG.get(slug)


def random_problem(
    rng: random.Random | None = None,
    *,
    exclude_slugs: frozenset[str] | set[str] | None = None,
) -> LeetProblem | None:
    """Return a random problem from the pool, optionally excluding some slugs.

    ``exclude_slugs`` lets the cog skip problems the user has already solved.
    Returns None only if every pooled problem is excluded.
    """
    picker = rng or random
    pool = PROBLEM_POOL
    if exclude_slugs:
        pool = [p for p in pool if p.slug not in exclude_slugs]
    if not pool:
        return None
    return picker.choice(pool)
