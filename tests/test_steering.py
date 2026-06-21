from unittest.mock import patch

from milky_frog.steering import (
    NullSteeringChannel,
    NullSteeringProducer,
    StdinSteeringChannel,
    StdinSteeringProducer,
    SteeringProducer,
)


def test_null_steering_producer_returns_inert_channel() -> None:
    producer = NullSteeringProducer()
    channel = producer.start()

    assert isinstance(channel, NullSteeringChannel)
    assert channel.drain() == []
    producer.stop(channel)


def test_null_steering_channel_standalone() -> None:
    channel = NullSteeringChannel()
    assert channel.drain() == []
    # drain is idempotent
    assert channel.drain() == []


def test_stdin_steering_producer_lifecycle() -> None:
    producer = StdinSteeringProducer()
    channel = producer.start()

    assert isinstance(channel, StdinSteeringChannel)
    channel._queue.put("steer")
    assert channel.drain() == ["steer"]
    producer.stop(channel)
    assert channel.drain() == []


def test_steering_producer_protocol() -> None:
    assert isinstance(NullSteeringProducer(), SteeringProducer)
    assert isinstance(StdinSteeringProducer(), SteeringProducer)


def test_stdin_steering_channel_supported_returns_false_on_windows() -> None:
    with patch("sys.platform", "win32"):
        channel = StdinSteeringChannel()
        assert channel._enabled is False


def test_stdin_steering_channel_supported_returns_false_on_non_tty() -> None:
    with patch("sys.stdin.isatty", return_value=False):
        channel = StdinSteeringChannel()
        assert channel._enabled is False


def test_stdin_steering_channel_supported_swallows_oserror() -> None:
    with patch("sys.stdin.isatty", side_effect=OSError):
        channel = StdinSteeringChannel()
        assert channel._enabled is False


def test_stdin_steering_channel_supported_swallows_valueerror() -> None:
    with patch("sys.stdin.isatty", side_effect=ValueError):
        channel = StdinSteeringChannel()
        assert channel._enabled is False


def test_stdin_steering_channel_start_when_not_enabled_is_noop() -> None:
    channel = StdinSteeringChannel()
    channel._enabled = False
    channel.start()
    assert channel._thread is None


def test_stdin_steering_channel_stop_when_thread_is_none() -> None:
    channel = StdinSteeringChannel()
    channel._enabled = True
    channel._thread = None
    channel._queue.put("orphan")
    channel.stop()
    # drain after stop must be empty — orphan line was purged
    assert channel.drain() == []


def test_stdin_steering_producer_stop_with_non_stdin_channel_is_noop() -> None:
    producer = StdinSteeringProducer()
    producer.stop(NullSteeringChannel())  # must not raise


def test_stdin_steering_channel_drain_is_idempotent() -> None:
    channel = StdinSteeringChannel()
    channel._queue.put("a")
    channel._queue.put("b")
    assert channel.drain() == ["a", "b"]
    assert channel.drain() == []


def test_null_steering_producer_stop_is_idempotent() -> None:
    producer = NullSteeringProducer()
    channel = producer.start()
    producer.stop(channel)
    # stopping twice must not raise
    producer.stop(channel)
