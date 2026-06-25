from __future__ import annotations

from typing import Never

type HandlerResult = Never
"""Placeholder for future per-step control returns (``BlockResult``, ``ApprovalResult``).

No variants are defined yet — handlers always return ``None``.  The event bus
collects ``HandlerResult`` values as infrastructure for future seams such as
``RunBeforeTool``.
"""
