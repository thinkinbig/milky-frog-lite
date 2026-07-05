from __future__ import annotations

import asyncio
from pathlib import Path
from typing import override

from milky_frog.core.handlers import HandlerDeps
from milky_frog.core.sandbox import SandboxFactory
from milky_frog.domain import VerificationNotice
from milky_frog.events.events import RunAfterTool
from milky_frog.events.hub import EventHub, Handler
from milky_frog.project import load_project_config

_TRIGGER_TOOLS = frozenset({"edit_file", "write_file"})


class VerificationHandler(Handler):
    """Runs configured verification commands after every successful edit Tool.

    Subscribes to ``RunAfterTool``. When ``call.name`` is ``edit_file`` or
    ``write_file`` and the tool succeeded (``is_error is False``), runs the
    per-workspace ``[verification].commands`` sequentially and returns a
    ``VerificationNotice``. The loop injects it as a synthetic tool result.

    When ``after_edit`` is disabled in the workspace config, this is a no-op.
    """

    def __init__(self, sandbox_factory: SandboxFactory) -> None:
        self._sandbox_factory = sandbox_factory

    @override
    def register(self, hub: EventHub) -> None:
        hub.on(RunAfterTool)(self._on_after_tool)

    async def _on_after_tool(
        self, event: RunAfterTool, deps: HandlerDeps
    ) -> VerificationNotice | None:
        if event.call.name not in _TRIGGER_TOOLS:
            return None
        if event.result.is_error:
            return None

        config = load_project_config(event.state.workspace)
        if not config.verification.after_edit:
            return None

        sandbox = self._sandbox_factory(event.state.workspace)
        env = sandbox.build_env()
        workspace: Path = event.state.workspace

        outputs: list[str] = []
        all_passed = True

        for cmd in config.verification.commands:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await proc.communicate()
            parts = [f"$ {cmd}"]
            if stdout:
                parts.append(stdout.decode(errors="replace").rstrip())
            if stderr:
                parts.append(stderr.decode(errors="replace").rstrip())
            outputs.append("\n".join(parts))
            if proc.returncode != 0:
                all_passed = False

        return VerificationNotice(
            summary="\n\n".join(outputs),
            exit_code_summary="all pass" if all_passed else "one or more commands FAILED",
        )
