# === 1. Fibonacci Number ===
from typing import List


def fib(n: int) -> int:
    """Return the n-th Fibonacci number (0-indexed: fib(0)=0, fib(1)=1).

    Time:  O(n)
    Space: O(1)
    """
    if n < 2:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


# Tests
assert fib(0) == 0
assert fib(1) == 1
assert fib(2) == 1
assert fib(10) == 55
assert fib(30) == 832040


# === 2. Climbing Stairs ===
def climb_stairs(n: int) -> int:
    """Return the number of distinct ways to climb an n-step staircase,
    taking 1 or 2 steps at a time.

    Time:  O(n)
    Space: O(1)
    """
    if n < 3:
        return n
    a, b = 1, 2
    for _ in range(3, n + 1):
        a, b = b, a + b
    return b


# Tests
assert climb_stairs(1) == 1
assert climb_stairs(2) == 2
assert climb_stairs(3) == 3
assert climb_stairs(4) == 5
assert climb_stairs(10) == 89


# === 3. Coin Change ===
def coin_change(coins: List[int], amount: int) -> int:
    """Return the fewest number of coins needed to make up the given amount.
    Return -1 if the amount cannot be made up by any combination of the coins.

    Coins may be reused unlimited times (unbounded knapsack).

    Time:  O(amount * len(coins))
    Space: O(amount)
    """
    INF = amount + 1
    dp = [INF] * (amount + 1)
    dp[0] = 0
    for coin in coins:
        for x in range(coin, amount + 1):
            if dp[x - coin] + 1 < dp[x]:
                dp[x] = dp[x - coin] + 1
    return dp[amount] if dp[amount] != INF else -1


# Tests
assert coin_change([1, 2, 5], 11) == 3   # 5 + 5 + 1
assert coin_change([2], 3) == -1
assert coin_change([1], 0) == 0
assert coin_change([1, 2, 5], 0) == 0
assert coin_change([186, 419, 83, 408], 6249) == 20


# === 4. Longest Increasing Subsequence ===
def length_of_lis(nums: List[int]) -> int:
    """Return the length of the longest strictly increasing subsequence.

    Uses patience sorting (binary search on tails array).

    Time:  O(n log n)
    Space: O(n)
    """
    import bisect

    tails: List[int] = []
    for x in nums:
        i = bisect.bisect_left(tails, x)
        if i == len(tails):
            tails.append(x)
        else:
            tails[i] = x
    return len(tails)


# Tests
assert length_of_lis([10, 9, 2, 5, 3, 7, 101, 18]) == 4   # [2, 3, 7, 101]
assert length_of_lis([0, 1, 0, 3, 2, 3]) == 4              # [0, 1, 2, 3]
assert length_of_lis([7, 7, 7, 7]) == 1
assert length_of_lis([]) == 0
assert length_of_lis([1]) == 1


# === 5. Longest Palindromic Substring ===
def longest_palindrome(s: str) -> str:
    """Return the longest palindromic substring in s.

    Expands around every possible centre (including between characters).

    Time:  O(n^2)
    Space: O(1)  (not counting the output)
    """
    def expand(l: int, r: int) -> str:
        """Expand outward from (l, r) while the window is a palindrome."""
        while l >= 0 and r < len(s) and s[l] == s[r]:
            l -= 1
            r += 1
        return s[l + 1 : r]

    best = ""
    for i in range(len(s)):
        # Odd-length palindromes (centre = i)
        p1 = expand(i, i)
        # Even-length palindromes (centre between i and i+1)
        p2 = expand(i, i + 1)
        candidate = p1 if len(p1) > len(p2) else p2
        if len(candidate) > len(best):
            best = candidate
    return best


# Tests
assert longest_palindrome("babad") in ("bab", "aba")
assert longest_palindrome("cbbd") == "bb"
assert longest_palindrome("a") == "a"
assert longest_palindrome("ac") in ("a", "c")
assert longest_palindrome("") == ""


# === 6. Valid Palindrome ===
def is_palindrome(s: str) -> bool:
    """Return True if s is a palindrome considering only alphanumeric
    characters and ignoring case.

    Time:  O(n)
    Space: O(1)
    """
    l, r = 0, len(s) - 1
    while l < r:
        while l < r and not s[l].isalnum():
            l += 1
        while l < r and not s[r].isalnum():
            r -= 1
        if s[l].lower() != s[r].lower():
            return False
        l += 1
        r -= 1
    return True


# Tests
assert is_palindrome("A man, a plan, a canal: Panama")
assert not is_palindrome("race a car")
assert is_palindrome(" ")
assert is_palindrome("")
assert is_palindrome(".,")
assert is_palindrome("0P") is False
