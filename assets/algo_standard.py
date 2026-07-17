"""Standard binary search."""
from __future__ import annotations

def binary_search_standard(arr: list[int], target: int) -> int:
    """Return the index of target in a sorted list, or -1 if not found."""
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
