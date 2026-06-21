from __future__ import annotations

from collections.abc import Iterator


class LangfuseClientFactory:
    """Stub Langfuse constructor that returns a fixed client."""

    def __init__(self, client: object) -> None:
        self._client = client

    def __call__(self, **kwargs: object) -> object:
        del kwargs
        return self._client


class RecordingLangfuseFactory:
    """Stub Langfuse constructor that records kwargs and returns a placeholder."""

    def __init__(self, calls: list[object], *, client: object | None = None) -> None:
        self._calls = calls
        self._client = client if client is not None else object()

    def __call__(self, **kwargs: object) -> object:
        self._calls.append(kwargs)
        return self._client


class NoOpKwargs:
    def __call__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


class NoOpArgsKwargs:
    def __call__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


class ScriptedPrompt:
    def __init__(
        self,
        lines: tuple[str, ...] | list[str],
        *,
        eof_on_exhaust: bool = True,
    ) -> None:
        self._lines: Iterator[str] = iter(lines)
        self._eof_on_exhaust = eof_on_exhaust

    def __call__(self) -> str:
        try:
            return next(self._lines)
        except StopIteration:
            if self._eof_on_exhaust:
                raise EOFError from None
            raise


class RecordingWelcome:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __call__(self, **kwargs: object) -> None:
        self._events.append(f"welcome:{kwargs['model']}")


class RecordingHelp:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __call__(self) -> None:
        self._events.append("help")


class RecordingAssistant:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __call__(self, message: str, **kwargs: object) -> None:
        self._events.append(f"answer:{message}:{kwargs['run_id']}")


class RecordingError:
    def __init__(self, messages: list[str]) -> None:
        self._messages = messages

    def __call__(self, message: str, **kwargs: object) -> None:
        del kwargs
        self._messages.append(message)
