from __future__ import annotations

import asyncio
import shlex

from pydantic import BaseModel, Field, JsonValue

from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext

_GIT_TIMEOUT_SECONDS = 30.0
_MAX_OUTPUT_BYTES = 128 * 1024
# Subcommands that only inspect repo state — they never mutate the working
# tree, index, refs, or history regardless of their arguments.
_GIT_READ_ONLY_SUBCOMMANDS = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "blame",
        "rev-parse",
        "grep",
        "ls-files",
        "ls-tree",
        "cat-file",
        "show-ref",
        "rev-list",
        "describe",
        "shortlog",
        "name-rev",
        "merge-base",
        "whatchanged",
        "ls-remote",
    }
)


class GitInput(BaseModel):
    command: str = Field(
        description="Git subcommand with arguments, e.g. 'status', 'diff', 'log --oneline -5'.",
    )


def _git_tokens(command: str) -> list[str] | None:
    command_str = command.strip()
    if not command_str:
        return None
    try:
        tokens = shlex.split(command_str)
    except ValueError:
        return None
    if not tokens:
        return None
    if tokens[0] == "git":
        tokens = tokens[1:]
    return tokens or None


def _git_branch_is_list_only(args: list[str]) -> bool:
    mutating = {"-d", "-D", "-m", "-M", "-c", "-C"}
    if any(flag in mutating for flag in args):
        return False
    return not any(not arg.startswith("-") for arg in args)


def _git_tag_is_list_only(args: list[str]) -> bool:
    if "-d" in args or "--delete" in args:
        return False
    return not any(not arg.startswith("-") for arg in args)


def _git_remote_is_list_only(args: list[str]) -> bool:
    mutating = {"add", "remove", "rm", "rename", "set-url", "set-branches", "update", "prune"}
    return not args or args[0] not in mutating


def _git_config_is_list_only(args: list[str]) -> bool:
    return "--list" in args or "-l" in args


def git_needs_approval(command: str) -> bool:
    """Return True when the git invocation may mutate repo state or history."""
    tokens = _git_tokens(command)
    if tokens is None:
        return True
    subcommand = tokens[0]
    args = tokens[1:]
    if subcommand in _GIT_READ_ONLY_SUBCOMMANDS:
        return False
    if subcommand == "branch":
        return not _git_branch_is_list_only(args)
    if subcommand == "tag":
        return not _git_tag_is_list_only(args)
    if subcommand == "remote":
        return not _git_remote_is_list_only(args)
    if subcommand == "config":
        return not _git_config_is_list_only(args)
    return True


class GitTool:
    """Run a git command inside the Workspace directory."""

    name = "git"
    requires_approval = True
    description = (
        "Run a git subcommand in the workspace repository and return its stdout. "
        "Read-only commands (status, diff, log, show, blame, rev-parse, grep, ls-files, "
        "cat-file, rev-list, listing branch/tag/remote, config --list) run immediately. "
        "Commands that modify the working tree or "
        "history (add, reset, commit, stash, branch creation, tag creation, etc.) pause the "
        "Run until the user approves. The command is executed with a clean, allow-listed "
        "environment."
    )
    input_model: type[BaseModel] = GitInput

    def needs_approval_for_call(self, arguments: dict[str, JsonValue]) -> bool:
        command = arguments.get("command")
        if not isinstance(command, str):
            return True
        return git_needs_approval(command)

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = GitInput.model_validate(input)
        tokens = _git_tokens(params.command)
        if tokens is None:
            if not params.command.strip():
                return ToolResult("empty git command", is_error=True)
            return ToolResult("invalid git command", is_error=True)
        sandbox = context.require_sandbox()
        env = sandbox.command_environment()
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                *tokens,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(sandbox.workspace),
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=_GIT_TIMEOUT_SECONDS
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                msg = f"git command timed out after {_GIT_TIMEOUT_SECONDS}s"
                return ToolResult(msg, is_error=True)
        except OSError as error:
            return ToolResult(f"failed to run git: {error}", is_error=True)

        if process.returncode != 0:
            error_text = stderr.decode("utf-8", errors="replace").strip() or "(no stderr)"
            return ToolResult(f"git exited {process.returncode}: {error_text}", is_error=True)

        stdout_text = stdout.decode("utf-8", errors="replace")
        if len(stdout) > _MAX_OUTPUT_BYTES:
            truncated = stdout_text[:_MAX_OUTPUT_BYTES]
            msg = (
                f"output truncated ({len(stdout)} bytes; "
                f"showing first {_MAX_OUTPUT_BYTES}):\n{truncated}"
            )
            return ToolResult(msg)
        return ToolResult(stdout_text if stdout_text else "(no output)")
