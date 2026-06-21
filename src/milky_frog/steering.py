from __future__ import annotations

import queue
import select
import sys
import threading
from typing import Protocol, runtime_checkable

from milky_frog.domain import SteeringChannel


class NullSteeringChannel:
    """Inert :class:`SteeringChannel` for sessions that own stdin elsewhere."""

    def drain(self) -> list[str]:
        return []


class StdinSteeringChannel:
    """Thread-backed :class:`SteeringChannel`: a background reader queues stdin
    lines for the active Run to drain between turns.

    Enabled only on a POSIX TTY, where ``select`` lets the reader wake to check
    its stop flag and hand stdin back promptly when the Run ends — a blocking
    ``readline`` would hold stdin until the next Enter, colliding with the
    between-turn prompt. Elsewhere it is inert and steering is simply off.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = self._supported()

    @staticmethod
    def _supported() -> bool:
        if sys.platform == "win32":
            return False
        try:
            return sys.stdin.isatty()
        except (OSError, ValueError):
            return False

    def start(self) -> None:
        if not self._enabled:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        # Drop any lines that arrived but were not drained before the Run ended,
        # so they cannot leak into the next prompt.
        self.drain()

    def drain(self) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._queue.get_nowait())
            except queue.Empty:
                return lines

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            line = sys.stdin.readline()
            if line == "":  # EOF (Ctrl-D)
                return
            text = line.strip()
            if text:
                self._queue.put(text)


@runtime_checkable
class SteeringProducer(Protocol):
    """Session-scoped factory for the :class:`SteeringChannel` used during one Run."""

    def start(self) -> SteeringChannel:
        """Acquire a channel for the foreground Run that is about to start."""
        ...

    def stop(self, channel: SteeringChannel) -> None:
        """Release the channel after the foreground Run ends."""
        ...


class NullSteeringProducer:
    """Producer for interactive sessions that read between-turn input via the UI."""

    _CHANNEL = NullSteeringChannel()

    def start(self) -> SteeringChannel:
        return self._CHANNEL

    def stop(self, channel: SteeringChannel) -> None:
        del channel


class StdinSteeringProducer:
    """Producer for headless Runs that accept mid-Run stdin steering.

    Not wired into the CLI today — there is no front-end affordance for blind
    mid-stream input. Pass explicitly to :class:`MilkyFrog` when experimenting or
    embedding Milky Frog in a host that owns concurrent stdin.
    """

    def start(self) -> SteeringChannel:
        channel = StdinSteeringChannel()
        channel.start()
        return channel

    def stop(self, channel: SteeringChannel) -> None:
        if isinstance(channel, StdinSteeringChannel):
            channel.stop()
