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
