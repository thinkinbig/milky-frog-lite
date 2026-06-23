# Compose Handlers via providers with a base lifetime

Settings-driven infrastructure Handlers (observability now; authorization and context build are expressed as `HandlerResult` control returns on the bus — see [ADR-0012](0012-shrink-handler-registry-to-a-read-only-lifecycle-bus.md)) are composed through an explicit one-line roster of provider types, and share resource lifetime through a `BaseHandler` base class — rather than the runtime naming each Handler concretely.

Previously `MilkyFrog` imported `LangfuseHandler` directly: it read `settings.langfuse.active`, constructed and registered it, and drove its `flush`/`finalize` from inside `run()`. Every Handler that needs lifecycle beyond pure event observation forced the runtime to grow concrete knowledge of it. The streaming UI Handlers were already decoupled (the CLI builds them and injects the registry), so the coupling was specifically about *infrastructure* Handlers with a resource lifetime.

We split the concern into a registrant bundle plus two lifetime scopes:

- **`BaseHandler`** (`handlers/registry.py`) — a cross-cutting bundle of Handlers. `register(registry)` is abstract (each bundle wires its own callbacks, in its own file); `aclose()` is a concrete **no-op default** that resource-holding bundles override. The no-op default lets the runtime release every bundle uniformly with no `isinstance` check.
- **Per-run** teardown is expressed as **events**: `LangfuseHandler` flushes its batch from the terminal lifecycle signals (`RunCompleted`/`RunFailed`/`RunCancelled`/`RunPaused`), which already fire from inside `Harness.run`. No runtime side-channel.
- **Instance-lifetime** teardown is the `BaseHandler.aclose` seam: `LangfuseHandler.aclose` flushes and shuts down its client. `MilkyFrog` is now a context manager that, on `__exit__`, `aclose`s every bundle it was handed on its reused event loop and then closes the loop.

Composition lives at the **composition root**, a `HandlerFactory` (`cli/factory.py`) that owns both the presentation dependency (a live `StreamingPrinter`) and `Settings`. Its `build()` composes the full bundle list — `StreamingHandlers` plus the settings-driven infrastructure — registers each onto one registry, and returns `(registry, bundles)`. The runtime is handed both; it no longer composes anything, it only owns the bundles' lifetime.

The infrastructure half stays in `handlers/assembly.py`: `InfrastructureHandlerAssembly._ROSTER` lists settings-driven Handler types (each implements `SettingsDrivenHandler.from_settings`); `build()` returns active bundles **unregistered** (the factory registers the whole roster uniformly). Adding an infrastructure Handler is a new file plus **one line** in the roster.

The factory must sit at the composition root rather than in `handlers/`: the UI bundle depends on `ui/`, which already imports `handlers/`, so centralizing UI and infrastructure composition any lower would invert the layering and create an import cycle.

We keep the roster explicit rather than import-time auto-discovery (a global decorator registry), upholding ADR-0004's instance-owned registry: the roster is the one place the full Handler set is readable and grep-able, and tests can build a registry with exactly the Handlers they want.

## Consequences

`MilkyFrog` no longer imports or composes any Handler; it receives the bundles and drives `aclose`. The Langfuse client — previously created in `__init__` and never closed — now closes on context exit, and the reused event loop closes there too (both were latent leaks). Because the `with` block wraps the whole session, teardown fires even when a Run raises, which is strictly more robust than the previous `except Exception: flush()`.

The full Handler roster is now readable in one place (`HandlerFactory._build_bundles`), and the runtime's only Handler responsibility is lifetime — a clean split between *who composes* (the composition root) and *who owns resources* (the runtime).

`BaseHandler` is a class-inheritance seam, a deliberate, contained departure from "seams are `typing.Protocol`s" (CLAUDE.md): these bundles are concrete cross-cutting registrants that share real behavior (the `aclose` default), not swappable adapters for an external dependency the Harness calls. The event registry and the Model/Tool/CheckpointStore seams remain Protocols.

The CLI's streaming Handlers also became a `BaseHandler` (`StreamingHandlers`) for uniformity, but stay caller-constructed (they close over the live `StreamingPrinter`, not `Settings`) and are injected via the existing `handlers` parameter — they are not in the settings-driven roster.

---

# 通过 provider 组装 Handler，并以基类承载生命周期

由配置驱动的基础设施 Handler（当前是可观测性；日后的授权等策略以 `Harness` 显式依赖注入，而非 Handler 总线）通过一份显式的、每个 Handler 一行的类型名册来组装，并通过 `BaseHandler` 基类共享资源生命周期——而不再由 runtime 具名地引用每个 Handler。

此前 `MilkyFrog` 直接 import `LangfuseHandler`：读取 `settings.langfuse.active`、构造并注册它，并在 `run()` 内部驱动其 `flush`/`finalize`。任何需要超出纯事件观察之生命周期的 Handler，都会迫使 runtime 增长对它的具体认知。流式 UI Handler 早已解耦（由 CLI 构造并注入 registry），因此真正的耦合点专指带资源生命周期的*基础设施* Handler。

我们将该关注点拆为一个注册者 bundle 加两个生命周期范围：

- **`BaseHandler`**（`handlers/registry.py`）——横切的 Handler bundle。`register(registry)` 为抽象方法（每个 bundle 在自己的文件里接线自己的回调）；`aclose()` 为具体的**空操作默认实现**，仅持有资源的 bundle 才覆写它。空操作默认让 runtime 无需 `isinstance` 即可统一释放全部 bundle。
- **单次 Run** 的收尾以**事件**表达：`LangfuseHandler` 在终止事件处理器（`RunCompleted`/`RunFailed`/`RunCancelled`/`RunPaused`，它们本就在 `Harness.run` 内部触发）中 flush 其批次。无 runtime 旁路。
- **实例生命周期**的收尾即 `BaseHandler.aclose` seam：`LangfuseHandler.aclose` flush 并关闭其 client。`MilkyFrog` 现为 context manager，`__exit__` 时在其复用的事件循环上 `aclose` 收到的每个 bundle，随后关闭事件循环。

组装发生在**组装根**——一个 `HandlerFactory`（`cli/factory.py`），它同时持有表现层依赖（实时的 `StreamingPrinter`）与 `Settings`。其 `build()` 组合出完整的 bundle 列表（`StreamingHandlers` 加上配置驱动的基础设施），将每个注册到同一 registry，并返回 `(registry, bundles)`。runtime 同时收到二者；它不再组装任何东西，只负责 bundle 的生命周期。

基础设施那一半仍住在 `handlers/assembly.py`：`InfrastructureHandlerAssembly._ROSTER` 列出由配置驱动的 Handler 类型（各实现 `SettingsDrivenHandler.from_settings`）；`build()` 返回活跃 bundle 的**未注册**列表（由 factory 统一注册整份名册）。新增一个基础设施 Handler = 一个新文件加名册里**一行**。

factory 必须位于组装根而非 `handlers/`：UI bundle 依赖 `ui/`，而 `ui/` 已经 import `handlers/`，因此若把 UI 与基础设施的组装下沉到更低层会倒置分层并造成循环 import。

我们保持名册显式，而非 import 期自动发现（全局装饰器注册表），以维护 ADR-0004 的实例持有 registry：名册是唯一能完整、可 grep 地读到 Handler 全集的地方，且测试可只装配自己想要的 Handler。

## 影响

`MilkyFrog` 不再 import 或组装任何 Handler；它接收 bundle 并驱动 `aclose`。此前在 `__init__` 创建、从不关闭的 Langfuse client 现在于 context 退出时关闭，复用的事件循环也在此关闭（二者原本都是潜在泄漏）。由于 `with` 块包裹整个会话，即便 Run 抛出异常收尾也会触发，比此前的 `except Exception: flush()` 更稳健。

完整的 Handler 名册现在集中可读于一处（`HandlerFactory._build_bundles`），而 runtime 对 Handler 的唯一职责是生命周期——*谁组装*（组装根）与*谁持有资源*（runtime）干净分离。

`BaseHandler` 是一个类继承式 seam，是对 "seam 应为 `typing.Protocol`"（CLAUDE.md）的有意且受限的偏离：这些 bundle 是共享真实行为（`aclose` 默认）的具体横切注册者，并非 Harness 调用的可替换外部依赖适配器。事件 registry 以及 Model/Tool/CheckpointStore seam 仍为 Protocol。

CLI 的流式 Handler 也改为 `BaseHandler`（`StreamingHandlers`）以求一致，但仍由调用方构造（它们闭包持有实时的 `StreamingPrinter` 而非 `Settings`），经既有的 `handlers` 参数注入——它们不在配置驱动的名册中。
