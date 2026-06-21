from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from milky_frog.domain import SteeringChannel
from milky_frog.infra.steering.producers import SteeringProducer


@contextmanager
def steering_channel(producer: SteeringProducer) -> Iterator[SteeringChannel]:
    """Acquire and release one Run-scoped :class:`~milky_frog.domain.SteeringChannel`."""
    channel = producer.start()
    try:
        yield channel
    finally:
        producer.stop(channel)
