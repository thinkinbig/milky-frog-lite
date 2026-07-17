# === 1. Two Sum ===
from typing import List


def two_sum(nums: List[int], target: int) -> List[int]:
    """Return indices of two numbers that add up to target.

    Complexity:
        Time: O(n) — single pass with hash map lookups.
        Space: O(n) — at most n entries in the hash map.
    """
    seen: dict[int, int] = {}
    for i, v in enumerate(nums):
        complement = target - v
        if complement in seen:
            return [seen[complement], i]
        seen[v] = i
    return []


# Test
assert two_sum([2, 7, 11, 15], 9) == [0, 1]
assert two_sum([3, 2, 4], 6) == [1, 2]
assert two_sum([3, 3], 6) == [0, 1]
assert two_sum([1, 2, 3], 7) == []


# === 2. Group Anagrams ===
from collections import defaultdict


def group_anagrams(strs: List[str]) -> List[List[str]]:
    """Group strings that are anagrams of each other.

    The key is a tuple of 26 character counts (guaranteed O(1) compare).  For
    Unicode-lowercase-only inputs this is fine; sorting the string would be
    O(k log k) instead of O(k).

    Complexity:
        Time: O(n * k) where k is the average string length.
        Space: O(n * k) for the dict values.
    """
    groups: dict[tuple[int, ...], list[str]] = defaultdict(list)
    for s in strs:
        counts = [0] * 26
        for ch in s:
            counts[ord(ch) - ord("a")] += 1
        groups[tuple(counts)].append(s)
    return list(groups.values())


# Test
result = group_anagrams(["eat", "tea", "tan", "ate", "nat", "bat"])
assert sorted(sorted(g) for g in result) == sorted(
    [sorted(g) for g in [["bat"], ["nat", "tan"], ["ate", "eat", "tea"]]]
)
assert group_anagrams([""]) == [[""]]
assert group_anagrams(["a"]) == [["a"]]


# === 3. Longest Substring Without Repeating Characters ===


def length_of_longest_substring(s: str) -> int:
    """Return the length of the longest substring without repeating chars.

    Sliding window with two pointers and a set tracking characters currently
    inside the window.

    Complexity:
        Time: O(n) — each character added/removed at most once.
        Space: O(min(n, |Σ|)) — window set, bounded by alphabet size.
    """
    seen: set[str] = set()
    left = 0
    max_len = 0
    for right, ch in enumerate(s):
        while ch in seen:
            seen.remove(s[left])
            left += 1
        seen.add(ch)
        max_len = max(max_len, right - left + 1)
    return max_len


# Test
assert length_of_longest_substring("abcabcbb") == 3  # "abc"
assert length_of_longest_substring("bbbbb") == 1  # "b"
assert length_of_longest_substring("pwwkew") == 3  # "wke"
assert length_of_longest_substring("") == 0
assert length_of_longest_substring(" ") == 1
assert length_of_longest_substring("au") == 2


# === 4. Product of Array Except Self ===


def product_except_self(nums: List[int]) -> List[int]:
    """Return array where answer[i] = product of all nums except nums[i].

    No division is used.  Each prefix product is written into the result
    array in the first pass; a running suffix product multiplies it in
    the second pass.

    Complexity:
        Time: O(n) — two linear passes.
        Space: O(1) — output array excluded from analysis.
    """
    n = len(nums)
    out = [1] * n

    # prefix pass
    prefix = 1
    for i in range(n):
        out[i] = prefix
        prefix *= nums[i]

    # suffix pass
    suffix = 1
    for i in range(n - 1, -1, -1):
        out[i] *= suffix
        suffix *= nums[i]

    return out


# Test
assert product_except_self([1, 2, 3, 4]) == [24, 12, 8, 6]
assert product_except_self([-1, 1, 0, -3, 3]) == [0, 0, 9, 0, 0]
assert product_except_self([2, 3]) == [3, 2]
assert product_except_self([5]) == [1]


# === 5. Maximum Subarray (Kadane's Algorithm) ===


def max_subarray(nums: List[int]) -> int:
    """Return the sum of the contiguous subarray with the largest sum.

    Kadane's algorithm: track the best sum ending at each position, and
    the global maximum seen so far.

    Complexity:
        Time: O(n) — single pass.
        Space: O(1) — two integers.
    """
    best = cur = nums[0]
    for v in nums[1:]:
        cur = v if cur < 0 else cur + v
        best = max(best, cur)
    return best


# Test
assert max_subarray([-2, 1, -3, 4, -1, 2, 1, -5, 4]) == 6  # [4, -1, 2, 1]
assert max_subarray([1]) == 1
assert max_subarray([5, 4, -1, 7, 8]) == 23
assert max_subarray([-1]) == -1
assert max_subarray([-2, -1]) == -1
