# Centralize handler assembly in one `session_handler_bundles` factory

[ADR-0008](0008-compose-handlers-via-providers-with-lifetime.md) envisioned a
`HandlerFactory` at the composition root plus an `InfrastructureHandlerAssembly`
roster. That machinery is no longer in the tree: composition had regressed into
**scattered, two-layer wiring**.

- `MilkyFrog.__init__` registered handlers inline in four separate statements:
  `PolicyHandler`, `SkillCatalogHandler`, the caller-supplied `bundles` loop, and
  a conditional `LangfuseHandler`.
- `Harness.__init__` *separately* self-registered `CheckpointHandler` onto the bus
  it was handed.

Two concrete problems followed. First, the full handler set was not readable in
one place — it spanned two layers. Second, **registration and lifetime tracking
had drifted apart**: `MilkyFrog._handlers` (the list `close()` iterates to
`aclose`) held only the user bundles and Langfuse. `PolicyHandler`,
`SkillCatalogHandler`, and `CheckpointHandler` were registered but never tracked,
so their `aclose` could never run (a latent leak, masked today only because those
three inherit the no-op default).

## Decision

Assemble every lifecycle handler bundle in **one module-level factory**,
`handlers/bundles.py::session_handler_bundles(settings, checkpoints, *, tool_policy,
extra)`. It returns the full ordered bundle list — `CheckpointHandler`,
`PolicyHandler`, `SkillCatalogHandler`, caller-supplied `extra`, then
`LangfuseHandler` (via its own `from_settings`, which owns the `active` check).

`MilkyFrog.__init__` calls it once and runs a **single loop that both registers
each bundle and stores it in `_handlers`** — so `close()` releases every bundle
uniformly. Ordering among priority-0 bundles is list order; `CheckpointHandler`
self-declares priority 100, so it persists first regardless of position.

`Harness` no longer self-wires `CheckpointHandler`. It still uses `checkpoints`
directly to claim and seed Runs, but persistence-on-lifecycle now travels through
the bus like every other observer: the `handlers` bus passed to `Harness` must
already carry a `CheckpointHandler`. `session_handler_bundles` is the one blessed path
that guarantees this; tests use a `make_harness` helper that wires it onto the
same bus they inspect.

We keep a **plain function** rather than reviving ADR-0008's factory/roster: there
is no longer a presentation dependency to co-locate (the UI subscribes its
renderer directly via `bus.subscribe` in `ui/tui/app.py`, not a `StreamingHandlers`
bundle), and nothing in the roster imports `ui/`, so the factory lives safely in
`handlers/` with no layering inversion. The roster stays explicit, not
import-time auto-discovery — upholding ADR-0004's instance-owned registry and
ADR-0012's single-publisher lifecycle bus.

## Consequences

- The `aclose` leak is fixed: `_handlers` now holds every bundle, so all are
  released on context exit.
- The full handler set is readable and grep-able in one place (`session_handler_bundles`).
- Persistence-on-lifecycle becomes a **caller responsibility**. A `Harness` built
  with a bus lacking `CheckpointHandler` will not persist (so won't resume). This
  is the deliberate trade-off of treating checkpointing as a handler rather than
  Harness-internal; mitigated by `session_handler_bundles` being the production path, the
  `make_harness` test helper, and a docstring warning on `Harness`.
- Supersedes ADR-0008's `HandlerFactory`/`_ROSTER` mechanism (already absent from
  the code); its `BaseHandler` lifetime split and ADR-0004's explicit-registry
  principle remain.

---

# 将 Handler 装配集中到单一 `session_handler_bundles` 工厂

[ADR-0008](0008-compose-handlers-via-providers-with-lifetime.md) 设想了位于组装根的
`HandlerFactory` 加上 `InfrastructureHandlerAssembly` 名册。那套机制已不在代码中：组装
退化成了**分散在两层的接线**。

- `MilkyFrog.__init__` 用四段独立语句内联注册：`PolicyHandler`、`SkillCatalogHandler`、
  调用方传入的 `bundles` 循环，以及条件性的 `LangfuseHandler`。
- `Harness.__init__` 则*另外*把 `CheckpointHandler` 自注册到收到的 bus 上。

由此带来两个具体问题。其一，完整的 handler 集无法在一处读到——它横跨两层。其二，
**注册与生命周期跟踪脱节了**：`MilkyFrog._handlers`（`close()` 遍历去 `aclose` 的列表）
只装了用户 bundle 和 Langfuse。`PolicyHandler`、`SkillCatalogHandler`、`CheckpointHandler`
被注册却从未被跟踪，它们的 `aclose` 永远无法执行（潜在泄漏，今天仅因这三者继承空操作
默认而被掩盖）。

## 决策

把每个 lifecycle handler bundle 集中到**单一模块级工厂**
`handlers/bundles.py::session_handler_bundles(settings, checkpoints, *, tool_policy,
extra)`。它返回完整有序的 bundle 列表——`CheckpointHandler`、`PolicyHandler`、
`SkillCatalogHandler`、调用方传入的 `extra`，最后是 `LangfuseHandler`（经其自身的
`from_settings`，由它负责 `active` 检查）。

`MilkyFrog.__init__` 调用它一次，并跑**一个同时完成注册与写入 `_handlers` 的循环**——
于是 `close()` 统一释放每个 bundle。priority-0 bundle 之间按列表顺序；`CheckpointHandler`
自带 priority 100，无论位置如何都最先持久化。

`Harness` 不再自注册 `CheckpointHandler`。它仍直接用 `checkpoints` 去 claim 和 seed Run，
但"按生命周期持久化"现在与其他观察者一样走 bus：传给 `Harness` 的 `handlers` bus 必须
已携带 `CheckpointHandler`。`session_handler_bundles` 是保证这一点的唯一正路；测试用 `make_harness`
helper，把它接到测试自己检视的同一条 bus 上。

我们保留**普通函数**而非复活 ADR-0008 的 factory/名册：已不再有需要就近放置的表现层依赖
（UI 在 `ui/tui/app.py` 直接经 `bus.subscribe` 订阅其 renderer，而非 `StreamingHandlers`
bundle），且名册中无一依赖 `ui/`，故工厂安全地住在 `handlers/`，不会倒置分层。名册保持
显式，而非 import 期自动发现——维护 ADR-0004 的实例持有 registry 与 ADR-0012 的只读总线。

## 影响

- `aclose` 泄漏被修复：`_handlers` 现含全部 bundle，context 退出时悉数释放。
- 完整 handler 集在一处可读、可 grep（`session_handler_bundles`）。
- "按生命周期持久化"成为**调用方责任**。用缺少 `CheckpointHandler` 的 bus 构造的
  `Harness` 将不持久化（因而无法 resume）。这是把 checkpoint 当 handler 而非 Harness 内置
  的有意取舍；以 `session_handler_bundles` 为生产正路、`make_harness` 测试 helper、以及 `Harness`
  docstring 警告来缓解。
- 取代 ADR-0008 的 `HandlerFactory`/`_ROSTER` 机制（已不在代码中）；其 `BaseHandler`
  生命周期拆分与 ADR-0004 的显式 registry 原则保留。
