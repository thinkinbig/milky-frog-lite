"""Lower bound & upper bound binary search."""
from __future__ import annotations

def binary_search_lower_bound(arr: list[int], target: int) -> int:
    """Return the first index where arr[i] >= target."""
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo

def binary_search_upper_bound(arr: list[int], target: int) -> int:
    """Return the first index where arr[i] > target."""
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo
