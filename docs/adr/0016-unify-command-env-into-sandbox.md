# Unify CommandEnvironment into Sandbox

Merge the separate `CommandEnvironment` (subprocess env builder) seam into the
existing **`Sandbox`** seam — the single injectable unit that changes when
moving from local to Docker execution.

## Supersedes (mechanism)

| ADR / issue | What changes |
|-------------|--------------|
| [0003](0003-start-with-a-policy-based-local-sandbox.md) | Path-violation policy unchanged; now lives on `Sandbox` together with `build_env()` |
| Issue #48 | `CommandEnvironment` merged into `Sandbox` before it shipped as a separate seam |

**Still authoritative for the Harness loop and lifecycle:** [0012](0012-shrink-handler-registry-to-a-read-only-lifecycle-bus.md), [0014](0014-persist-checkpoints-as-runstate-snapshots.md), [0015](0015-centralize-handler-assembly-in-default-handlers.md).

## The problem

Local execution needs three things that all break under Docker:

1. **Path resolution** — `Sandbox.resolve()` returns a host absolute `Path`;
   inside a container the same path is meaningless.
2. **Command environment** — `CommandEnvironment.build()` reads host
   `os.environ`; a container has its own environment.
3. **Command execution** — `BashTool` uses `openpty()` + `asyncio.create_subprocess_shell`;
   Docker would need `docker exec`.

Issue #47's design review concluded that each of these is **never** swapped in
isolation — a Docker sandbox would replace all three at once. Separate
`Sandbox` + `CommandEnvironment` seams would mean two wiring ducts and two
factories for a single switch.

## The decision

**Extend the `Sandbox` Protocol** (keep the name) to wrap:

- `workspace: Path`
- `config: ProjectConfig`
- `resolve(relative_path, *, allow_sensitive=False) -> Path`
- `build_env() -> dict[str, str]`

`LocalSandbox` (`harness/sandbox/base.py`) implements deny-pattern path policy
*and* the allowlist env builder (including `_NONINTERACTIVE_ENV`). The
`CommandEnvironment` Protocol is removed.

`ToolContext` holds one `sandbox` field instead of separate `sandbox` +
`command_env`. Tools call `sandbox.resolve()` for path checks and
`sandbox.build_env()` for the subprocess env.

```python
class Sandbox(Protocol):
    workspace: Path
    config: ProjectConfig
    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path: ...
    def build_env(self) -> dict[str, str]: ...

class SandboxFactory(Protocol):
    def __call__(self, workspace: Path) -> Sandbox: ...
```

Injected via `sandbox_factory` on `AgentSessionConfig` / `AgentHarness`.

## Consequences

**Positive**

- One seam to swap for a future `DockerSandbox`.
- One wiring path through `AgentHarness` → `AgentLoop` → `ToolContext`.
- Keeps the **Local Sandbox** product term aligned with the code module name.
- `SandboxViolation` unchanged.

**Negative**

- `LocalSandbox` does both path policy and env building (cheap).
- File Tools carry `build_env()` on the Protocol though they never call it.

**Risks**

- Reversible if path policy and env building genuinely diverge on a future backend.
- `Sandbox` is a policy/execution seam, **not** host isolation (ADR-0003).

---

# 将 CommandEnvironment 并入 Sandbox

将独立的 `CommandEnvironment`（子进程环境构建器）seam 并入既有 **`Sandbox`**
seam —— 从本地切换到 Docker 执行时，需要替换的单一可注入单元。

## 决定

**保留 `Sandbox` 名称**，扩展 Protocol：

- `workspace: Path`
- `config: ProjectConfig`
- `resolve(relative_path, *, allow_sensitive=False) -> Path`
- `build_env() -> dict[str, str]`

`LocalSandbox`（`harness/sandbox/base.py`）同时实现拒绝模式路径策略与 allowlist
环境构建器（含 `_NONINTERACTIVE_ENV`）。`CommandEnvironment` Protocol 移除。

`ToolContext` 持有单个 `sandbox` 字段。Tool 调用 `sandbox.resolve()` 与
`sandbox.build_env()`。

通过 `AgentSessionConfig` / `AgentHarness` 的 `sandbox_factory` 注入。

## 影响

**正面**

- 未来 `DockerSandbox` 只需替换一个 seam。
- 产品术语 **Local Sandbox** 与代码模块名一致。
- `SandboxViolation` 保持不变。

**负面**

- `LocalSandbox` 同时承担路径策略与环境构建（开销很小）。

**风险**

- 若未来 backend 确实需要拆分，可逆。
- `Sandbox` 是策略/执行 seam，不是宿主机隔离（ADR-0003）。
