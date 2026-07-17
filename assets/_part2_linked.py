from __future__ import annotations

from collections import OrderedDict
from typing import Optional


class ListNode:
    """Singly-linked list node used by questions 1 and 2."""

    def __init__(self, val: int = 0, next: Optional[ListNode] = None) -> None:
        self.val = val
        self.next = next


# ---------------------------------------------------------------------------
# Helper functions  (list ↔ ListNode)
# ---------------------------------------------------------------------------


def _list_to_pylist(head: Optional[ListNode]) -> list[int]:
    """Convert a linked list into a Python list."""
    result: list[int] = []
    cur = head
    while cur is not None:
        result.append(cur.val)
        cur = cur.next
    return result


def _list_from_pylist(values: list[int]) -> Optional[ListNode]:
    """Build a linked list from a Python list and return its head."""
    if not values:
        return None
    head = ListNode(values[0])
    cur = head
    for v in values[1:]:
        cur.next = ListNode(v)
        cur = cur.next
    return head


# === 1. Reverse Linked List (iterative) ===


def reverse_list(head: Optional[ListNode]) -> Optional[ListNode]:
    """Reverse a singly-linked list in-place, iteratively.

    Complexity:
        Time: O(n)
        Space: O(1)
    """
    prev: Optional[ListNode] = None
    cur = head
    while cur is not None:
        nxt = cur.next
        cur.next = prev
        prev = cur
        cur = nxt
    return prev


# Test
_head = _list_from_pylist([1, 2, 3, 4, 5])
_rev = reverse_list(_head)
assert _list_to_pylist(_rev) == [5, 4, 3, 2, 1]
assert _list_to_pylist(reverse_list(None)) == []
assert _list_to_pylist(reverse_list(ListNode(42))) == [42]


# === 2. Merge Two Sorted Lists ===


def merge_two_lists(
    list1: Optional[ListNode], list2: Optional[ListNode]
) -> Optional[ListNode]:
    """Merge two sorted linked lists into one sorted list.

    Uses a dummy sentinel node to simplify the merge.

    Complexity:
        Time: O(m + n)
        Space: O(1)
    """
    dummy = ListNode()
    tail = dummy

    while list1 is not None and list2 is not None:
        if list1.val <= list2.val:
            tail.next = list1
            list1 = list1.next
        else:
            tail.next = list2
            list2 = list2.next
        tail = tail.next

    # Attach the remaining nodes.
    tail.next = list1 if list1 is not None else list2
    return dummy.next


# Test
_l1 = _list_from_pylist([1, 2, 4])
_l2 = _list_from_pylist([1, 3, 4])
assert _list_to_pylist(merge_two_lists(_l1, _l2)) == [1, 1, 2, 3, 4, 4]
assert _list_to_pylist(merge_two_lists(None, _list_from_pylist([0]))) == [0]
assert _list_to_pylist(merge_two_lists(None, None)) == []


# === 3. Valid Parentheses ===


def is_valid_parentheses(s: str) -> bool:
    """Determine if the input string has valid bracket ordering.

    Complexity:
        Time: O(n)
        Space: O(n)
    """
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []

    for ch in s:
        if ch in pairs:
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
        else:
            stack.append(ch)

    return not stack


# Test
assert is_valid_parentheses("()") is True
assert is_valid_parentheses("()[]{}") is True
assert is_valid_parentheses("(]") is False
assert is_valid_parentheses("([)]") is False
assert is_valid_parentheses("{[]}") is True
assert is_valid_parentheses("") is True


# === 4. LRU Cache ===


class LRUCache:
    """Least-recently-used cache with O(1) get and put.

    Backed by OrderedDict (insertion-order + move-to-end semantics).

    Complexity:
        get: O(1) amortised
        put: O(1) amortised
    """

    def __init__(self, capacity: int) -> None:
        self._cap = capacity
        self._cache: OrderedDict[int, int] = OrderedDict()

    def get(self, key: int) -> int:
        """Return the value for *key*, or -1 if missing."""
        if key not in self._cache:
            return -1
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: int, value: int) -> None:
        """Insert or update *key*; evict LRU entry when over capacity."""
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._cap:
            self._cache.popitem(last=False)


# Test
_cache = LRUCache(2)
_cache.put(1, 1)
_cache.put(2, 2)
assert _cache.get(1) == 1
_cache.put(3, 3)  # evicts key 2
assert _cache.get(2) == -1
_cache.put(4, 4)  # evicts key 1
assert _cache.get(1) == -1
assert _cache.get(3) == 3
assert _cache.get(4) == 4


# === 5. Evaluate Reverse Polish Notation ===


def eval_rpn(tokens: list[str]) -> int:
    """Evaluate an expression in Reverse Polish Notation.

    Division truncates toward zero (int(a / b) in Python 3).

    Complexity:
        Time: O(n)
        Space: O(n)
    """
    stack: list[int] = []
    ops = {"+", "-", "*", "/"}

    for t in tokens:
        if t in ops:
            b = stack.pop()
            a = stack.pop()
            if t == "+":
                stack.append(a + b)
            elif t == "-":
                stack.append(a - b)
            elif t == "*":
                stack.append(a * b)
            else:  # "/"
                stack.append(int(a / b))  # truncates toward zero
        else:
            stack.append(int(t))

    return stack[0]


# Test
assert eval_rpn(["2", "1", "+", "3", "*"]) == 9
assert eval_rpn(["4", "13", "5", "/", "+"]) == 6
assert (
    eval_rpn(["10", "6", "9", "3", "+", "-11", "*", "/", "*", "17", "+", "5", "+"])
    == 22
)
assert eval_rpn(["3", "-4", "+"]) == -1
