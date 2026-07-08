from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from milky_frog.adapters.docker.cli import DockerCliResult
from milky_frog.adapters.local import LocalSandbox
from milky_frog.checkpoint import CheckpointStore
from milky_frog.core.runtime.assemble import make_agent_harness
from milky_frog.core.sandbox import (
    CommandOutcome,
    CommandPresentation,
    CommandResult,
    CommandTimeout,
    Sandbox,
)
from milky_frog.domain import (
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    StreamDone,
    TextDelta,
    TokenUsage,
    ToolCall,
)
from milky_frog.events import EventHub
from milky_frog.handlers.checkpoint import CheckpointHandler
from milky_frog.harness.harness import AgentHarness
from milky_frog.harness.prompt_context import ContextLoader
from milky_frog.harness.tools import ToolContext, ToolRegistry, ToolResult
from milky_frog.models import Model

# ── Harness builder ───────────────────────────────────────────────────


class RecordingSandboxFactory:
    """SandboxFactory that records the workspaces it was called with.

    Lets tests assert the Harness actually routes sandbox construction through
    the injected factory instead of hardcoding ``LocalSandbox``.
    """

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, workspace: Path) -> Sandbox:
        self.calls.append(workspace)
        return LocalSandbox(workspace)


def make_harness(
    model: Model,
    tools: ToolRegistry,
    checkpoints: CheckpointStore,
    hub: EventHub | None = None,
    sandbox_factory: RecordingSandboxFactory | None = None,
    context_loader: ContextLoader | None = None,
) -> AgentHarness:
    """Build a Harness with checkpointing wired, mirroring production assembly.

    Production wires ``CheckpointHandler`` via ``make_session_handlers``; the
    Harness no longer self-registers it. Tests that need a resumable Run use this
    helper so the snapshot handler lands on the same hub they inspect.
    """
    bus = hub if hub is not None else EventHub()
    CheckpointHandler(checkpoints).register(bus)
    harness = make_agent_harness(
        model,
        checkpoints,
        bus,
        tools=tools,
        sandbox_factory=sandbox_factory or LocalSandbox,
        context_loader=context_loader,
    )
    harness.policy.auto_approve()
    return harness


# ── Tool stubs ────────────────────────────────────────────────────────


class EchoInput(BaseModel):
    text: str


class EchoTool:
    name = "echo"
    description = "Echo text"
    input_model: type[BaseModel] = EchoInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        assert context.workspace.is_dir()
        parsed = EchoInput.model_validate(input)
        return ToolResult(parsed.text)


# ── Model stubs ───────────────────────────────────────────────────────


class FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.calls += 1
        if self.calls == 1:
            assert request.tools[0]["function"]["name"] == "echo"
            yield StreamDone(
                ModelResponse(tool_calls=(ToolCall("call-1", "echo", {"text": "hello"}),))
            )
            return
        yield TextDelta("done")
        yield StreamDone(ModelResponse(content="done"))


class ReasoningModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield ReasoningDelta("weighing options")
        yield TextDelta("the answer")
        yield StreamDone(ModelResponse(content="the answer", reasoning="weighing options"))


class IdentityCapturingModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        assert request.messages[0].role.value == "system"
        assert "Milky Frog" in request.messages[0].content
        assert "奶蛙" in request.messages[0].content
        assert request.messages[1].role.value == "user"
        assert request.messages[1].content == "Who are you?"
        yield TextDelta("I am Milky Frog.")
        yield StreamDone(ModelResponse(content="I am Milky Frog."))


class InvalidToolArgsThenRecoverModel:
    """First turn requests a Tool with invalid arguments; second turn sees the error."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.calls += 1
        if self.calls == 1:
            yield StreamDone(ModelResponse(tool_calls=(ToolCall("call-1", "echo", {}),)))
            return
        tool_messages = [message for message in request.messages if message.role.value == "tool"]
        assert tool_messages
        assert "ValidationError" in tool_messages[-1].content
        yield StreamDone(ModelResponse(content="recovered"))


class UsageReportingModel:
    """Reports token usage per call: one tool turn, then a final answer turn."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        if self.calls == 1:
            yield StreamDone(
                ModelResponse(
                    tool_calls=(ToolCall("call-1", "echo", {"text": "hello"}),),
                    usage=TokenUsage(input_tokens=100, output_tokens=20),
                )
            )
            return
        yield StreamDone(
            ModelResponse(
                content="done",
                usage=TokenUsage(input_tokens=160, output_tokens=30, cached_tokens=64),
            )
        )


class EarlyStreamDoneModel:
    """Yields StreamDone before trailing chunks to assert early stream exit."""

    def __init__(self) -> None:
        self.extra_chunks_yielded = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        yield StreamDone(ModelResponse(content="done"))
        for index in range(995):
            self.extra_chunks_yielded += 1
            yield TextDelta(f"extra-{index}")


class SlowStreamModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield TextDelta("partial")
        await asyncio.sleep(0.05)
        yield StreamDone(ModelResponse(content="done"))


class PauseThenFinishModel:
    """A tool turn first, then a final answer — to pause at a 1-call budget."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        if self.calls == 1:
            yield StreamDone(
                ModelResponse(tool_calls=(ToolCall("call-1", "echo", {"text": "hi"}),))
            )
            return
        yield StreamDone(ModelResponse(content="done"))


class ContinuationModel:
    """Completes at once, asserting the latest user turn is visible in context."""

    def __init__(self, expected_user: str) -> None:
        self.expected_user = expected_user

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        users = [m for m in request.messages if m.role.value == "user"]
        assert users[-1].content == self.expected_user
        yield StreamDone(ModelResponse(content="ack"))


class FlakyConnectionModel:
    """Fails the first *failures* stream attempts with ``ConnectionError``."""

    def __init__(self, *, failures: int = 2) -> None:
        self._failures_left = failures
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        if self._failures_left > 0:
            self._failures_left -= 1
            raise ConnectionError("offline")
        yield TextDelta("ok")
        yield StreamDone(ModelResponse(content="ok"))


class ImmediateErrorModel:
    """Always raises the configured error on stream."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        raise self._error
        yield StreamDone(ModelResponse())  # makes this an async generator; never reached


# ── Langfuse stubs ───────────────────────────────────────────────────


class LangfuseClientFactory:
    """Stub Langfuse constructor that returns a fixed client."""

    def __init__(self, client: object) -> None:
        self._client = client

    def __call__(self, **kwargs: object) -> object:
        del kwargs
        return self._client


# ── Sandbox stubs ────────────────────────────────────────────────────


class FixedOutcomeSandbox:
    """Sandbox wrapper that returns a canned CommandOutcome from run_command.

    Lets a Tool test exercise every ``CommandOutcome`` branch without spawning
    a real process. Path resolution and config still come from a real
    ``LocalSandbox`` so deny-policy behaviour is unchanged.
    """

    def __init__(self, workspace: Path, outcome: CommandOutcome) -> None:
        self._inner = LocalSandbox(workspace)
        self._outcome = outcome
        self.workspace = self._inner.workspace
        self.config = self._inner.config

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path:
        return self._inner.resolve(relative_path, allow_sensitive=allow_sensitive)

    def build_env(self) -> dict[str, str]:
        return self._inner.build_env()

    async def run_command(
        self,
        command: str,
        *,
        timeout_seconds: float,
        presentation: CommandPresentation = CommandPresentation.PLAIN,
    ) -> CommandOutcome:
        del command, timeout_seconds, presentation
        return self._outcome


class RecordingCommandSandbox:
    """Sandbox that records commands and reports success without running them."""

    def __init__(self, workspace: Path, recorder: list[str]) -> None:
        self._inner = LocalSandbox(workspace)
        self._recorder = recorder
        self.workspace = self._inner.workspace
        self.config = self._inner.config

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path:
        return self._inner.resolve(relative_path, allow_sensitive=allow_sensitive)

    def build_env(self) -> dict[str, str]:
        return self._inner.build_env()

    async def run_command(
        self,
        command: str,
        *,
        timeout_seconds: float,
        presentation: CommandPresentation = CommandPresentation.PLAIN,
    ) -> CommandOutcome:
        del timeout_seconds, presentation
        self._recorder.append(command)
        return CommandResult(exit_code=0, output=f"ran {command}")


class RecordingCommandSandboxFactory:
    """SandboxFactory yielding RecordingCommandSandbox, sharing one command log."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def __call__(self, workspace: Path) -> Sandbox:
        return RecordingCommandSandbox(workspace, self.commands)


class TimingOutSandboxFactory:
    """SandboxFactory whose sandboxes always report a CommandTimeout."""

    def __call__(self, workspace: Path) -> Sandbox:
        return FixedOutcomeSandbox(workspace, CommandTimeout(seconds=1.0))


# ── Docker CLI stubs ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CombinedCall:
    """One recorded ``DockerCli.combined`` invocation."""

    argv: list[str]
    timeout_seconds: float


class StubDockerCli:
    """DockerCli double: records argv, returns canned results. No daemon needed."""

    def __init__(
        self,
        *,
        container_id: str = "container-1",
        outcome: CommandOutcome | None = None,
    ) -> None:
        self._container_id = container_id
        self._outcome = outcome if outcome is not None else CommandResult(0, "ok\n")
        self.captured: list[list[str]] = []
        self.combined_calls: list[CombinedCall] = []

    async def capture(self, argv: Sequence[str]) -> DockerCliResult:
        self.captured.append(list(argv))
        stdout = f"{self._container_id}\n" if argv[:2] == ["docker", "run"] else ""
        return DockerCliResult(exit_code=0, stdout=stdout, stderr="")

    async def combined(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandOutcome:
        self.combined_calls.append(CombinedCall(list(argv), timeout_seconds))
        return self._outcome
