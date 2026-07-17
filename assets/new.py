"""Binary Search Algorithm Collection

This module provides multiple variants of binary search, each implemented
as a standalone function. All assume the input list is sorted unless
otherwise noted.
"""

from __future__ import annotations


# ── 1. Standard Binary Search ──────────────────────────────────────────────

def binary_search_standard(arr: list[int], target: int) -> int:
    """Return the index of target in a sorted list, or -1 if not found.

    Performs standard binary search on a pre-sorted list of integers.
    Expects arr to be sorted in ascending order.

    Args:
        arr: A list of integers sorted in ascending order.
        target: The integer value to search for.

    Returns:
        The index (0-based) of target if found, otherwise -1.
    """
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1


# ── 2. Lower Bound & Upper Bound ──────────────────────────────────────────

def binary_search_lower_bound(arr: list[int], target: int) -> int:
    """Return the first index where arr[i] >= target.

    If every element is < target, return len(arr).  Assumes arr is sorted
    in non-decreasing order.
    """
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def binary_search_upper_bound(arr: list[int], target: int) -> int:
    """Return the first index where arr[i] > target.

    If every element is <= target, return len(arr).  Assumes arr is sorted
    in non-decreasing order.
    """
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo


# ── 3. Rotated Sorted Array ───────────────────────────────────────────────

def binary_search_rotated(arr: list[int], target: int) -> int:
    """Search for target in a rotated sorted array of distinct ints.

    Returns the index of target, or -1 if not found.
    """
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        if arr[lo] <= arr[mid]:
            if arr[lo] <= target < arr[mid]:
                hi = mid - 1
            else:
                lo = mid + 1
        else:
            if arr[mid] < target <= arr[hi]:
                lo = mid + 1
            else:
                hi = mid - 1
    return -1


def find_min_in_rotated(arr: list[int]) -> int:
    """Find the minimum element in a rotated sorted array of distinct ints."""
    lo, hi = 0, len(arr) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] > arr[hi]:
            lo = mid + 1
        else:
            hi = mid
    return arr[lo]


# ── 4. Binary Search on Answer / Mountain Peak ────────────────────────────

def binary_search_on_answer(
    nums: list[int],
    target: int,
    *,
    find_first: bool = True,
) -> int | None:
    """Find the smallest/largest index where a monotonic predicate holds.

    The predicate is: nums[i] >= target  (for the default case).
    When *find_first* is True (default) the smallest index satisfying
    the predicate is returned (lower bound).  When *find_first* is False
    the largest index satisfying the predicate is returned (upper bound).

    Returns None when no index satisfies the predicate.

    >>> binary_search_on_answer([1, 3, 5, 7, 9], 5)
    2
    >>> binary_search_on_answer([1, 3, 5, 7, 9], 6)
    3
    >>> binary_search_on_answer([1, 3, 5, 7, 9], 10) is None
    True
    """
    if not nums:
        return None
    if find_first:
        lo, hi = 0, len(nums) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if nums[mid] >= target:
                hi = mid
            else:
                lo = mid + 1
        return lo if nums[lo] >= target else None
    else:
        lo, hi = 0, len(nums) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if nums[mid] <= target:
                lo = mid
            else:
                hi = mid - 1
        return lo if nums[lo] <= target else None


def find_peak_in_mountain(arr: list[int]) -> int | None:
    """Return the index of the peak in a mountain array.

    A *mountain array* is strictly increasing up to a peak element and
    strictly decreasing afterwards.  This function finds that peak in
    O(log n) time via binary search.

    Returns None when the array has fewer than three elements (no valid
    mountain) or when the mountain property does not hold.

    >>> find_peak_in_mountain([0, 1, 3, 5, 4, 2])
    3
    >>> find_peak_in_mountain([0, 2, 1, 0])
    1
    """
    if len(arr) < 3:
        return None
    lo, hi = 0, len(arr) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < arr[mid + 1]:
            lo = mid + 1
        else:
            hi = mid
    if (lo == 0 or lo == len(arr) - 1) or not (
        arr[lo - 1] < arr[lo] > arr[lo + 1]
    ):
        return None
    return lo
