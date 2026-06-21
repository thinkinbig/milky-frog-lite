from __future__ import annotations

from typing import Protocol, runtime_checkable

from milky_frog.domain import SteeringChannel
from milky_frog.infra.steering.channels import NullSteeringChannel, StdinSteeringChannel


@runtime_checkable
class SteeringProducer(Protocol):
    """Session-scoped factory for a :class:`~milky_frog.domain.SteeringChannel`."""

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
