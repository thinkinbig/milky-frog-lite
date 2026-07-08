# Container Sandbox (DockerSandbox) — Design

Status: approved, pending implementation plan
Issue: [thinkinbig/milky-frog-lite#60](https://github.com/thinkinbig/milky-frog-lite/issues/60)
Date: 2026-07-08

## Background

`docs/ARCHITECTURE.md` §6/§7 already names a single `Sandbox` seam
(`core/sandbox.py`) with `LocalSandbox` (`adapters/local/sandbox.py`) as the
only adapter today. The seam's stated purpose from day one is that path
resolution, environment construction, and command execution change together
when moving from local to containerized execution — they must not be split
into separate swappable pieces (see project memory
`docker-execution-backend-direction`).

Two things are true of the current codebase that this design addresses:

1. **Command execution isn't on the seam yet.** `Sandbox` only defines
   `resolve()` and `build_env()`. `BashTool` (`harness/tools/builtins/bash.py`)
   and `VerificationHandler` (`handlers/verification.py`) each call
   `asyncio.create_subprocess_shell` directly, only pulling `build_env()` /
   `workspace` off the sandbox. A prior attempt to close this
   (commit `4d47a88`, "Extract sandbox command runner seam", on the unmerged
   local branch `refactor/sandbox-command-runner`) drafted exactly the
   `run_command()` addition this design uses as its starting point; it never
   merged because a concurrent PR (#67, bash asyncio-pipe rework) landed
   first and touched the same file.
2. **MCP (merged same day as this issue's last comment, PR #80)** spawns MCP
   server subprocesses directly via the `mcp` SDK's `stdio_client`, entirely
   bypassing `Sandbox`. This is a real, current instance of the same
   host-escape risk the issue's discussion thread flagged as a future
   concern for a `spawn()`-style seam addition. This design does **not**
   close that gap (see Non-goals) — it's called out explicitly so it isn't
   mistaken for solved.

## Goals

- A containerized `Sandbox` adapter (`DockerSandbox`) implementing the same
  `Sandbox` protocol as `LocalSandbox`, injectable via the existing
  `sandbox_factory` seam with no change to `AgentHarness`, `AgentLoop`, or
  any Tool's signature.
- `bash` executes inside the container; timeout and truncation behavior
  matches `LocalSandbox` from the Tool's point of view.
- Post-edit verification commands (`[verification].commands`) also execute
  inside the container when the Docker backend is active — today they run
  via their own host subprocess call, which is the same class of gap `bash`
  had before this design's `run_command()` extraction.
- File Tools (`read_file`, `write_file`, `edit_file`, `grep`, `list_dir`)
  work unmodified against a bind-mounted workspace.
- Opt-in via `.milky-frog/config.toml`; `LocalSandbox` remains the default.

## Non-goals

- **MCP servers routed through Sandbox.** `McpClientManager` continues to
  spawn its own host subprocesses. Closing this requires a materially
  different seam capability (a long-lived, bidirectionally-piped process
  handle — `spawn(argv) -> ProcessHandle` — not a one-shot
  `run_command()`), which the issue's own discussion thread treats as an
  open design question needing more research. Tracked as a follow-up issue,
  not silently dropped.
- **AIO Sandbox (agent-infra/sandbox) as the backend.** Evaluated per the
  issue's discussion; deferred because its workspace file-access model
  (bind mount vs. upload/sync API) and auth/network exposure model aren't
  confirmed. Plain `docker exec` requires no such research and satisfies
  every acceptance criterion. `DockerSandbox`'s adapter internals could be
  swapped for an AIO-backed implementation later without changing the
  `Sandbox` protocol.
- **Full multi-tenant / orchestration, network isolation, seccomp, rootless
  containers.** Explicitly out of scope per the issue.
- **Replacing `LocalSandbox` as the default.** Docker stays opt-in.
- **Pydantic's Monty** (Rust Python-subset interpreter) — evaluated during
  design and rejected as unrelated: it sandboxes *Python-language execution*
  for a "Code Mode" tool-orchestration pattern, not arbitrary shell/toolchain
  execution (`git`, `uv`, `pytest`, …), which is what `bash` and verification
  need. Not a substitute for container-based execution here.

## Design

### 1. `Sandbox.run_command()` — protocol extension (shared by Local and Docker)

Ported from the `4d47a88` draft onto current `main`:

- `core/sandbox.py` gains:
  - `CommandPresentation` (`StrEnum`: `PLAIN`, `TERMINAL`)
  - `CommandResult(exit_code, output, display_output)`,
    `CommandTimeout(seconds)`, `CommandStartError(message)` — frozen
    dataclasses
  - `CommandOutcome = CommandResult | CommandTimeout | CommandStartError`
  - `Sandbox.run_command(command, *, timeout_seconds, presentation=PLAIN) -> CommandOutcome`
    added to the Protocol
- `adapters/local/command.py` (new): `run_local_command()` — the
  subprocess-shell + ANSI-strip + presentation-env + timeout-kill logic
  extracted verbatim from today's `bash.py`.
- `adapters/local/sandbox.py`: `LocalSandbox.run_command()` delegates to
  `run_local_command(workspace=self.workspace, env=self.build_env(), ...)`.
- `harness/tools/builtins/bash.py`: reduces to calling
  `sandbox.run_command(command, timeout_seconds=..., presentation=TERMINAL)`
  and dispatching on the `CommandOutcome` variant — removes the ~100 lines
  of duplicated subprocess/ANSI/timeout logic.
- `handlers/verification.py`: the per-command
  `asyncio.create_subprocess_shell` loop is replaced with
  `sandbox.run_command(cmd, timeout_seconds=...)` per configured command.

Net effect: command execution has exactly one implementation path per
`Sandbox` adapter. Both places that run shell commands today (`bash`,
post-edit verification) go through it, so swapping in `DockerSandbox` moves
both into the container consistently — no half-containerized state where
`bash` runs isolated but `uv run pytest` silently runs on the host.

### 2. `DockerSandbox` adapter

**File I/O — unchanged Tools.** `DockerSandbox` composes an internal
`LocalSandbox` for `resolve()`, `workspace`, and `config`: same deny-pattern
policy (`.env`, `.git`, `.milkyfrogignore`, …), same host-path resolution.
The workspace is bind-mounted into the container, so host-side file I/O
(what `read_file`/`write_file`/`edit_file`/`grep`/`list_dir` already do)
sees the same files the container sees. **No builtin file Tool changes.**
Only `run_command()` is container-specific.

**Container lifecycle — persistent, reused.**
- `DockerSandboxFactory` (implements `SandboxFactory`) owns a process-wide
  registry: `dict[(workspace, image), container_id]` behind an
  `asyncio.Lock`.
- On first `run_command()` for a given `(workspace, image)`, lazily:
  `docker run -d --name milky-frog-<slug> -v <host_workspace>:<workspace_mount> -w <workspace_mount> <image> sleep infinity`
  — cache the container id.
- Every subsequent `run_command()` is
  `docker exec -w <workspace_mount> <env flags> <container> sh -c "<command>"`
  — same `CommandOutcome` contract as `LocalSandbox`, so `bash.py` needs no
  backend-specific branching.
- Rationale: `docker exec` (~tens of ms) vs. a fresh `docker run` per call
  (hundreds of ms–1s) matters for an interactive agent loop calling `bash`
  repeatedly. No shell state persists between separate `bash` calls today
  either way (each is already a fresh subprocess), so reuse only buys
  startup latency and toolchain-install persistence within a container,
  not a behavior change.
- Timeout: enforced the same way as `LocalSandbox` — `asyncio.wait_for`
  around the host-side `docker exec` subprocess. On timeout the *host-side*
  `docker exec` client process is killed; the in-container process may
  linger until the container itself is torn down. This is a known MVP
  limitation, documented alongside the existing "policy boundary, not host
  isolation" caveat.

**Env.** `DockerSandbox.build_env()` does **not** forward host
`HOME`/`PATH`/`SHELL`/`TERM`/`LANG` — those point at the wrong filesystem
inside a differently-shaped container image. It returns the non-interactive
defaults (`CI=true`, `GIT_TERMINAL_PROMPT=0`) plus
`config.env_allowlist_extra` values read from the *host* environment (opt-in
tokens/build vars — forwarding the value, not a host path, is correct).

**Teardown.** `DockerSandboxFactory.aclose()` stops and removes every
container it started. `ShutdownManager.wire()` gains an optional
`sandbox_factory` parameter; if it exposes `aclose()` (duck-typed), `cleanup()`
awaits it — mirrors the existing MCP-manager and model-client cleanup in the
same method.

### 3. Config schema

New nested model in `project.py`, following the existing
`CheckpointConfig`/`VerificationConfig` pattern (own TOML sub-table, own
validation scope):

```toml
[sandbox]
kind = "docker"                    # local | docker (default: local)
image = "python:3.12-bookworm"     # required when kind = "docker"; no default
workspace_mount = "/mnt/workspace"  # must live under /mnt
```

```python
class SandboxConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["local", "docker"] = "local"
    image: str | None = None
    workspace_mount: str = "/mnt/workspace"

    @field_validator("workspace_mount")
    @classmethod
    def _require_mnt_prefix(cls, v: str) -> str:
        if not v.startswith("/mnt"):
            raise ValueError("workspace_mount must live under /mnt")
        return v

    @model_validator(mode="after")
    def _require_image_for_docker(self) -> SandboxConfig:
        if self.kind == "docker" and not self.image:
            raise ValueError("image is required when sandbox.kind = 'docker'")
        return self
```

`ProjectConfig.sandbox: SandboxConfig = SandboxConfig()`.

**Failure mode:** unlike the rest of `ProjectConfig` (whole-file parse
failures fall back to all-defaults silently), a `[sandbox]` table that fails
its own validation (e.g. `kind = "docker"` with a missing/invalid `image`)
**raises a clear config error at startup** instead of silently downgrading
to an unsandboxed `LocalSandbox` run. A user who configured Docker isolation
and mistyped `image` should see an error, not silently lose the isolation
they asked for. This is a deliberate, narrow exception to the
existing config-is-always-lenient rule, scoped to `[sandbox]` only.

Config key naming uses `kind`, not `backend`/`execution` — "ExecutionBackend"
was already introduced and explicitly reverted as a seam name in this
repo's history (commit `5ab16f2`, "rename ExecutionBackend back to Sandbox
seam"), and `CONTEXT.md`'s `Provider` entry separately lists `backend` under
terms to avoid.

### 4. Wiring

`core/runtime/assemble.py` / `app/session.py`: `AgentSessionConfig.sandbox_factory`
is the existing injection point — no signature change. A new
`make_sandbox_factory(config: ProjectConfig) -> SandboxFactory` picks
`LocalSandbox` or `DockerSandboxFactory(image=config.sandbox.image,
workspace_mount=config.sandbox.workspace_mount)` based on
`config.sandbox.kind`, replacing the hardcoded `LocalSandbox` default at
session construction.

`milky-frog doctor`: when `config.sandbox.kind == "docker"`, add one
diagnostic — `docker version` reachable — alongside the existing
config-diagnostic checks, so a missing/misconfigured Docker daemon fails at
`doctor` time rather than on the first `bash` call mid-Run.

## Documentation updates

- `docs/ARCHITECTURE.md` §6 seam table: `DockerSandbox` row updated from
  "future" to shipped. §7 gets a "Container Sandbox" subsection: bind-mount
  + `docker exec` model, and the same "policy boundary, not full isolation"
  caveat extended to containers (a compromised process can still reach the
  bind-mounted workspace; we don't mount the Docker socket into the
  container, so container escape via that vector isn't possible by
  construction, but this is not a security boundary against a
  fully-untrusted model).
- `CONTEXT.md`: new glossary entry **Container Sandbox** alongside
  **Local Sandbox**. `_Avoid_: DockerExecutionBackend, execution backend`.
- `README.md`: "Containerized execution (opt-in)" section — config snippet,
  prerequisite (`docker` CLI on `PATH`), and the timeout/lingering-process
  caveat from §2 above.

## Testing

- Unit: `LocalSandbox.run_command` / `BashTool` / `VerificationHandler`
  tests retargeted to the new seam (per the `4d47a88` draft's
  `test_sandbox.py` additions — command execution, terminal presentation,
  timeout).
- `DockerSandbox` unit tests stub the `docker` CLI invocation (a named stub
  class in `tests/stubs.py`, no bare lambdas per repo convention), verifying
  constructed `docker run`/`docker exec` argv, lazy container reuse across
  multiple `run_command()` calls for the same workspace, and env-forwarding
  (host `HOME`/`PATH` never appear; `env_allowlist_extra` values do).
- Integration: `tests/integration/test_docker_sandbox.py`, skipped unless
  `docker` is actually available (`shutil.which("docker")` + a live
  `docker version` check). Covers `bash`, `read_file`, `grep` against a real
  container per the issue's acceptance criteria. Not part of the default
  CI run unless the runner has Docker (optional CI job).

## Acceptance criteria (from the issue)

- [ ] Containerized `Sandbox` implementation + factory injectable via
      `AgentSessionConfig.sandbox_factory`
- [ ] `bash` executes in the container; cwd is the mounted workspace;
      timeout/truncation behavior matches local
- [ ] `resolve()` rejects `.env`/`.git`/etc. sensitive paths, same as
      `LocalSandbox`
- [ ] `read_file`/`grep` behave predictably under the container sandbox
      (mount assumptions documented)
- [ ] Docs: README/docs explain how to enable the container backend
- [ ] Tests + `ruff` + `pyrefly` pass
