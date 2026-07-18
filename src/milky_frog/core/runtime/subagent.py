"""Nested Run composition: child Workspace provisioning and Harness execution."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal
from uuid import uuid4

from milky_frog.adapters.docker import DockerSandboxFactory
from milky_frog.adapters.local import LocalSandbox
from milky_frog.core.cleanup import complete_cleanup
from milky_frog.core.runtime.assemble import HarnessAssembly
from milky_frog.domain import DEFAULT_MAX_MODEL_CALLS, RunCancellation, RunRequest
from milky_frog.harness.subagent_worktree import (
    SubagentWorktree,
    create_worktree,
    finalize_worktree,
    git_docker_mounts,
)
from milky_frog.harness.tools import ToolRegistry
from milky_frog.harness.tools.builtins import (
    SubagentOutcome,
    SubagentRejected,
    read_only_tools,
    write_subagent_tools,
)
from milky_frog.project import ProjectConfig, load_project_config

logger = logging.getLogger(__name__)


class SubagentRuntime:
    """Run nested work behind the ``SubagentRunner`` interface.

    A read-only nested Run reuses the parent Workspace with a restricted Tool
    registry. A write nested Run provisions a child Workspace using a git
    worktree, composes a Container Sandbox for that Workspace, runs a temporary
    Harness, and then finalizes or preserves the worktree.

    Worktree and Sandbox are therefore peers in composition: the worktree
    provides the child Workspace; the Sandbox supplies its execution policy.
    """

    def __init__(self, assembly: HarnessAssembly, *, jina_api_key: str | None = None) -> None:
        self._assembly = assembly
        self._jina_api_key = jina_api_key
        self._read_only_harness = assembly.make_harness(
            ToolRegistry(read_only_tools(jina_api_key=jina_api_key)),
            auto_approve=True,
        )

    async def __call__(
        self,
        prompt: str,
        capability: Literal["read_only", "write"],
        max_model_calls: int | None,
        cancellation: RunCancellation | None,
        workspace: Path,
        parent_run_id: str,
    ) -> SubagentOutcome:
        calls = DEFAULT_MAX_MODEL_CALLS if max_model_calls is None else max_model_calls
        if capability == "read_only":
            result = await self._read_only_harness.run(
                self._request(prompt, workspace, calls, cancellation, parent_run_id)
            )
            return SubagentOutcome(result)
        return await self._run_write(
            prompt,
            workspace,
            calls,
            cancellation,
            parent_run_id,
        )

    async def _run_write(
        self,
        prompt: str,
        parent_workspace: Path,
        max_model_calls: int,
        cancellation: RunCancellation | None,
        parent_run_id: str,
    ) -> SubagentOutcome:
        config = load_project_config(parent_workspace)
        self._require_container_sandbox(config)
        management_sandbox = LocalSandbox(parent_workspace, config)
        worktree = await create_worktree(
            management_sandbox,
            parent_workspace,
            uuid4().hex,
        )

        sandbox_factory: DockerSandboxFactory | None = None
        try:
            sandbox_factory = self._make_worktree_sandbox(config, worktree)
            harness = self._assembly.make_harness(
                ToolRegistry(
                    write_subagent_tools(jina_api_key=self._jina_api_key, home=self._assembly.home)
                ),
                sandbox_factory=sandbox_factory,
                auto_approve=True,
            )
            result = await harness.run(
                self._request(
                    prompt,
                    worktree.path,
                    max_model_calls,
                    cancellation,
                    parent_run_id,
                )
            )
        except BaseException:
            if sandbox_factory is not None:
                await self._close_after_failure(sandbox_factory)
            await self._finalize_after_failure(management_sandbox, worktree)
            raise

        try:
            await complete_cleanup(
                sandbox_factory.aclose(),
                propagate_cancellation=True,
            )
        except BaseException:
            await self._finalize_after_failure(management_sandbox, worktree)
            raise

        outcome = await complete_cleanup(
            finalize_worktree(management_sandbox, worktree),
            propagate_cancellation=True,
        )
        return SubagentOutcome(
            result,
            worktree=worktree.path,
            branch=worktree.branch,
            worktree_kept=outcome.kept,
        )

    @staticmethod
    def _request(
        prompt: str,
        workspace: Path,
        max_model_calls: int,
        cancellation: RunCancellation | None,
        parent_run_id: str,
    ) -> RunRequest:
        return RunRequest(
            prompt=prompt,
            workspace=workspace,
            max_model_calls=max_model_calls,
            cancellation=cancellation,
            run_kind="subagent",
            parent_run_id=parent_run_id,
        )

    @staticmethod
    def _require_container_sandbox(config: ProjectConfig) -> None:
        if config.sandbox.kind != "docker":
            raise SubagentRejected('subagent write capability requires [sandbox].kind = "docker"')
        if config.sandbox.image is None:
            raise SubagentRejected(
                'subagent write capability requires [sandbox].image when [sandbox].kind = "docker"'
            )

    @staticmethod
    def _make_worktree_sandbox(
        config: ProjectConfig,
        worktree: SubagentWorktree,
    ) -> DockerSandboxFactory:
        image = config.sandbox.image
        if image is None:  # pragma: no cover - checked before worktree creation
            raise SubagentRejected("subagent write capability requires [sandbox].image")
        return DockerSandboxFactory(
            image=image,
            workspace_mount=config.sandbox.workspace_mount,
            mask_paths=config.sandbox.mask_paths,
            config=config,
            extra_mounts=git_docker_mounts(worktree),
        )

    @staticmethod
    async def _close_after_failure(sandbox_factory: DockerSandboxFactory) -> None:
        """Best-effort close that preserves the exception already in flight."""
        try:
            await complete_cleanup(
                sandbox_factory.aclose(),
                propagate_cancellation=False,
            )
        except BaseException:
            logger.exception("failed to close nested Run Container Sandbox")

    @staticmethod
    async def _finalize_after_failure(
        management_sandbox: LocalSandbox,
        worktree: SubagentWorktree,
    ) -> None:
        """Best-effort cleanup that never masks the nested Run failure."""
        try:
            outcome = await complete_cleanup(
                finalize_worktree(management_sandbox, worktree),
                propagate_cancellation=False,
            )
        except BaseException:
            logger.exception("failed to finalize nested Run worktree %s", worktree.path)
            return
        if outcome.kept:
            logger.warning(
                "nested Run failed after producing work; preserved %s on %s",
                worktree.path,
                worktree.branch,
            )
