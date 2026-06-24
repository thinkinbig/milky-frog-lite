# Unify Sandbox and CommandEnvironment into ExecutionBackend

Merge the two separate `Sandbox` (path-resolution policy) and
`CommandEnvironment` (subprocess env builder) seams into one
**`ExecutionBackend`** seam — the single injectable unit that changes when
moving from local to Docker execution.

## Supersedes (mechanism)

| ADR | What changes |
|-----|--------------|
| [0003](0003-start-with-a-policy-based-local-sandbox.md) | `Sandbox` protocol absorbed by `ExecutionBackend`; path-violation policy kept |
| [ADR-0016 design note in AGENTS.md](../AGENTS.md) | Previous `CommandEnvironment` seam (issue #48) merged into `ExecutionBackend` before it shipped independently |

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
isolation — a Docker backend would replace all three at once. Three separate
seams would mean three separate wiring ducts, three factories, and three
injection points for a single backend switch.

Meanwhile issue #48 had already introduced `CommandEnvironment` as
a standalone seam (sandbox-adjacent but not merged). That was the right
incremental step to ship config-driven env allowlisting, but the Y2
step of #48 is **not** to keep them separate — it is to merge them into
`ExecutionBackend` before either seam accumulates more wiring.

## The decision

**Define a single `ExecutionBackend` Protocol** that wraps:

- `workspace: Path`
- `resolve(relative_path, *, allow_sensitive=False) -> Path`
- `build_env() -> dict[str, str]`

`LocalExecutionBackend` absorbs `LocalSandbox`'s deny-pattern path policy
*and* `LocalCommandEnvironment`'s allowlist env builder (including
`_NONINTERACTIVE_ENV`). The old `Sandbox` and `CommandEnvironment` Protocols
are removed; concrete classes are deleted.

`ToolContext` holds one `backend` field instead of two (`sandbox` +
`command_env`). Tool implementations call `backend.resolve()` for path
checks and `backend.build_env()` for the subprocess env.

The `ExecutionBackendFactory` Protocol replaces `SandboxFactory`:

```python
class ExecutionBackend(Protocol):
    workspace: Path
    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path: ...
    def build_env(self) -> dict[str, str]: ...

class ExecutionBackendFactory(Protocol):
    def __call__(self, workspace: Path) -> ExecutionBackend: ...
```

## Consequences

**Positive**

- One seam to swap for a future `DockerExecutionBackend`.
- One wiring path through `AgentHarness` → `AgentLoop` → `ToolContext`.
- Less code: two Protocols + two concrete classes become one of each.
- No change to `SandboxViolation` (exception name kept for backward compat
  in Tools).

**Negative**

- Slightly larger default-construction footprint for `LocalExecutionBackend`
  (it now does both path policy and env building), but both are cheap.
- Tools that only need path resolution (read/write/edit/list_dir) carry the
  `build_env()` method they never call. This is a minor Protocol-broadening,
  but acceptable for a lite agent — the alternative is three separate
  Protocols with three wiring ducts.

**Risks**

- If a future backend genuinely needs different implementations for path
  resolution vs env (e.g. `resolve()` is always local but `build_env()` is
  container-specific), we can split again. The ADR is reversible.
- `ExecutionBackend` is a seam for **execution**, not a security boundary.
  `SandboxViolation` is still the enforcement mechanism; `ExecutionBackend`
  just groups it with env building.

---

# 将 Sandbox 和 CommandEnvironment 统一为 ExecutionBackend

将两个独立的 `Sandbox`（路径解析策略）和 `CommandEnvironment`（子进程环境构建器）
seam 合并为一个 **`ExecutionBackend`** seam —— 从本地切换到 Docker 执行时，
需要替换的单一可注入单元。

## 决定

定义单个 `ExecutionBackend` Protocol，包含：

- `workspace: Path`
- `resolve(relative_path, *, allow_sensitive=False) -> Path`
- `build_env() -> dict[str, str]`

`LocalExecutionBackend` 吸收 `LocalSandbox` 的拒绝模式路径策略和
`LocalCommandEnvironment` 的 allowlist 环境构建器（包括 `_NONINTERACTIVE_ENV`）。
旧的 `Sandbox` 和 `CommandEnvironment` Protocol 被移除；具体类被删除。

`ToolContext` 持有单个 `backend` 字段而非两个（`sandbox` + `command_env`）。
Tool 实现调用 `backend.resolve()` 进行路径检查，调用 `backend.build_env()`
获取子进程环境。

`ExecutionBackendFactory` Protocol 替代 `SandboxFactory`：

## 影响

**正面**

- 未来 `DockerExecutionBackend` 只需替换一个 seam。
- 一条接线路径通过 `AgentHarness` → `AgentLoop` → `ToolContext`。
- 更少代码：两个 Protocol + 两个具体类变成一个协议 + 一个具体类。
- `SandboxViolation` 异常名称保留以保持 Tool 向后兼容。

**负面**

- `LocalExecutionBackend` 默认构建略微变大（同时做路径策略和环境构建），
  但两者都轻量。
- 仅需路径解析的 Tool（read/write/edit/list_dir）携带它们从不调用的
  `build_env()` 方法。这有点 Protocol 扩大化，但对 lite agent 可接受——
  替代方案是三个独立的 Protocol 和三套接线管道。

**风险**

- 如果未来某个 backend 确实需要路径解析与环境构建的不同实现，可以再拆分。
  本 ADR 是可逆的。
- `ExecutionBackend` 是**执行** seam，而非安全边界。`SandboxViolation` 仍是
  执行机制；`ExecutionBackend` 只是将其与环境构建分组。
