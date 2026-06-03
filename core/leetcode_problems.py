"""The curated problem pool for the /leet feature — the official LeetCode 75.

This is the editable seed list. It mirrors LeetCode's official **LeetCode 75**
study plan (https://leetcode.com/studyplan/leetcode-75/): 75 problems, all free
(no Premium-locked entries), grouped by the plan's own categories for
readability. Every slug + title + difficulty was pulled straight from LeetCode's
study-plan API, not guessed.

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


# The official LeetCode 75 study plan, verified live against leetcode.com — all
# free. Grouped by the plan's categories; selection is random so order has no
# functional meaning.
PROBLEM_POOL: list[LeetProblem] = [
    # Array / String
    LeetProblem("merge-strings-alternately", "Merge Strings Alternately", "Easy"),
    LeetProblem("greatest-common-divisor-of-strings", "Greatest Common Divisor of Strings", "Easy"),
    LeetProblem("kids-with-the-greatest-number-of-candies", "Kids With the Greatest Number of Candies", "Easy"),
    LeetProblem("can-place-flowers", "Can Place Flowers", "Easy"),
    LeetProblem("reverse-vowels-of-a-string", "Reverse Vowels of a String", "Easy"),
    LeetProblem("reverse-words-in-a-string", "Reverse Words in a String", "Medium"),
    LeetProblem("product-of-array-except-self", "Product of Array Except Self", "Medium"),
    LeetProblem("increasing-triplet-subsequence", "Increasing Triplet Subsequence", "Medium"),
    LeetProblem("string-compression", "String Compression", "Medium"),
    # Two Pointers
    LeetProblem("move-zeroes", "Move Zeroes", "Easy"),
    LeetProblem("is-subsequence", "Is Subsequence", "Easy"),
    LeetProblem("container-with-most-water", "Container With Most Water", "Medium"),
    LeetProblem("max-number-of-k-sum-pairs", "Max Number of K-Sum Pairs", "Medium"),
    # Sliding Window
    LeetProblem("maximum-average-subarray-i", "Maximum Average Subarray I", "Easy"),
    LeetProblem("maximum-number-of-vowels-in-a-substring-of-given-length", "Maximum Number of Vowels in a Substring of Given Length", "Medium"),
    LeetProblem("max-consecutive-ones-iii", "Max Consecutive Ones III", "Medium"),
    LeetProblem("longest-subarray-of-1s-after-deleting-one-element", "Longest Subarray of 1's After Deleting One Element", "Medium"),
    # Prefix Sum
    LeetProblem("find-the-highest-altitude", "Find the Highest Altitude", "Easy"),
    LeetProblem("find-pivot-index", "Find Pivot Index", "Easy"),
    # Hash Map / Set
    LeetProblem("find-the-difference-of-two-arrays", "Find the Difference of Two Arrays", "Easy"),
    LeetProblem("unique-number-of-occurrences", "Unique Number of Occurrences", "Easy"),
    LeetProblem("determine-if-two-strings-are-close", "Determine if Two Strings Are Close", "Medium"),
    LeetProblem("equal-row-and-column-pairs", "Equal Row and Column Pairs", "Medium"),
    # Stack
    LeetProblem("removing-stars-from-a-string", "Removing Stars From a String", "Medium"),
    LeetProblem("asteroid-collision", "Asteroid Collision", "Medium"),
    LeetProblem("decode-string", "Decode String", "Medium"),
    # Queue
    LeetProblem("number-of-recent-calls", "Number of Recent Calls", "Easy"),
    LeetProblem("dota2-senate", "Dota2 Senate", "Medium"),
    # Linked List
    LeetProblem("delete-the-middle-node-of-a-linked-list", "Delete the Middle Node of a Linked List", "Medium"),
    LeetProblem("odd-even-linked-list", "Odd Even Linked List", "Medium"),
    LeetProblem("reverse-linked-list", "Reverse Linked List", "Easy"),
    LeetProblem("maximum-twin-sum-of-a-linked-list", "Maximum Twin Sum of a Linked List", "Medium"),
    # Binary Tree - DFS
    LeetProblem("maximum-depth-of-binary-tree", "Maximum Depth of Binary Tree", "Easy"),
    LeetProblem("leaf-similar-trees", "Leaf-Similar Trees", "Easy"),
    LeetProblem("count-good-nodes-in-binary-tree", "Count Good Nodes in Binary Tree", "Medium"),
    LeetProblem("path-sum-iii", "Path Sum III", "Medium"),
    LeetProblem("longest-zigzag-path-in-a-binary-tree", "Longest ZigZag Path in a Binary Tree", "Medium"),
    LeetProblem("lowest-common-ancestor-of-a-binary-tree", "Lowest Common Ancestor of a Binary Tree", "Medium"),
    # Binary Tree - BFS
    LeetProblem("binary-tree-right-side-view", "Binary Tree Right Side View", "Medium"),
    LeetProblem("maximum-level-sum-of-a-binary-tree", "Maximum Level Sum of a Binary Tree", "Medium"),
    # Binary Search Tree
    LeetProblem("search-in-a-binary-search-tree", "Search in a Binary Search Tree", "Easy"),
    LeetProblem("delete-node-in-a-bst", "Delete Node in a BST", "Medium"),
    # Graphs - DFS
    LeetProblem("keys-and-rooms", "Keys and Rooms", "Medium"),
    LeetProblem("number-of-provinces", "Number of Provinces", "Medium"),
    LeetProblem("reorder-routes-to-make-all-paths-lead-to-the-city-zero", "Reorder Routes to Make All Paths Lead to the City Zero", "Medium"),
    LeetProblem("evaluate-division", "Evaluate Division", "Medium"),
    # Graphs - BFS
    LeetProblem("nearest-exit-from-entrance-in-maze", "Nearest Exit from Entrance in Maze", "Medium"),
    LeetProblem("rotting-oranges", "Rotting Oranges", "Medium"),
    # Heap / Priority Queue
    LeetProblem("kth-largest-element-in-an-array", "Kth Largest Element in an Array", "Medium"),
    LeetProblem("smallest-number-in-infinite-set", "Smallest Number in Infinite Set", "Medium"),
    LeetProblem("maximum-subsequence-score", "Maximum Subsequence Score", "Medium"),
    LeetProblem("total-cost-to-hire-k-workers", "Total Cost to Hire K Workers", "Medium"),
    # Binary Search
    LeetProblem("guess-number-higher-or-lower", "Guess Number Higher or Lower", "Easy"),
    LeetProblem("successful-pairs-of-spells-and-potions", "Successful Pairs of Spells and Potions", "Medium"),
    LeetProblem("find-peak-element", "Find Peak Element", "Medium"),
    LeetProblem("koko-eating-bananas", "Koko Eating Bananas", "Medium"),
    # Backtracking
    LeetProblem("letter-combinations-of-a-phone-number", "Letter Combinations of a Phone Number", "Medium"),
    LeetProblem("combination-sum-iii", "Combination Sum III", "Medium"),
    # DP - 1D
    LeetProblem("n-th-tribonacci-number", "N-th Tribonacci Number", "Easy"),
    LeetProblem("min-cost-climbing-stairs", "Min Cost Climbing Stairs", "Easy"),
    LeetProblem("house-robber", "House Robber", "Medium"),
    LeetProblem("domino-and-tromino-tiling", "Domino and Tromino Tiling", "Medium"),
    # DP - Multidimensional
    LeetProblem("unique-paths", "Unique Paths", "Medium"),
    LeetProblem("longest-common-subsequence", "Longest Common Subsequence", "Medium"),
    LeetProblem("best-time-to-buy-and-sell-stock-with-transaction-fee", "Best Time to Buy and Sell Stock with Transaction Fee", "Medium"),
    LeetProblem("edit-distance", "Edit Distance", "Medium"),
    # Bit Manipulation
    LeetProblem("counting-bits", "Counting Bits", "Easy"),
    LeetProblem("single-number", "Single Number", "Easy"),
    LeetProblem("minimum-flips-to-make-a-or-b-equal-to-c", "Minimum Flips to Make a OR b Equal to c", "Medium"),
    # Trie
    LeetProblem("implement-trie-prefix-tree", "Implement Trie (Prefix Tree)", "Medium"),
    LeetProblem("search-suggestions-system", "Search Suggestions System", "Medium"),
    # Intervals
    LeetProblem("non-overlapping-intervals", "Non-overlapping Intervals", "Medium"),
    LeetProblem("minimum-number-of-arrows-to-burst-balloons", "Minimum Number of Arrows to Burst Balloons", "Medium"),
    # Monotonic Stack
    LeetProblem("daily-temperatures", "Daily Temperatures", "Medium"),
    LeetProblem("online-stock-span", "Online Stock Span", "Medium"),
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
