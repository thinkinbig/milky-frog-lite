# Use typed event handlers

Cross-cutting Harness behavior will be implemented as typed event Handlers registered on an instance-owned registry with explicit priority, rather than `next()`-based middleware chains or global decorator state. Authorization, checkpointing, and observability need deterministic lifecycle interception, but do not require an onion execution model.

Handlers register on two channels:

- **`observe`** (and the `on` / `subscribe` aliases) — read-only inspection; may not return outcomes.
- **`intercept`** — may return typed outcomes (`BlockTool`, `TransformContext`, `PatchToolResult`) that the Harness applies on `BeforeTool`, `BeforeModel`, and `AfterTool` only.

Wildcard `subscribe` handlers participate in the same priority ordering as type-specific observe handlers.

## Consequences

Decorators are registration syntax only; they register functions against events such as `BeforeTool`, `AfterTool`, and `RunFailed`. Observe Handlers inspect events and record state; intercept Handlers may reject an operation or transform context/results. Ordering remains visible and testable. Intercept return values on other event types are ignored with a warning.

---

# 使用类型化事件 Handler

Harness 的横切行为将通过类型化事件 Handler 实现。Handler 注册到实例持有的 registry，并具有显式优先级；不使用基于 `next()` 的 middleware 链或全局装饰器状态。授权、checkpoint 和可观测性需要确定性的生命周期拦截，但不需要洋葱式执行模型。

Handler 通过两个通道注册：

- **`observe`**（以及 `on` / `subscribe` 别名）——只读观察，不得返回 outcome。
- **`intercept`**——可返回 typed outcome（`BlockTool`、`TransformContext`、`PatchToolResult`），Harness 仅在 `BeforeTool`、`BeforeModel`、`AfterTool` 上应用。

wildcard `subscribe` Handler 与类型特定的 observe Handler 使用同一套 priority 排序。

## 影响

装饰器仅作为注册语法，将函数注册到 `BeforeTool`、`AfterTool` 和 `RunFailed` 等事件。observe Handler 检查事件并记录状态；intercept Handler 可拒绝操作或变换 context/result。执行顺序保持可见且可测试。在其他事件类型上返回 intercept outcome 会被忽略并记录 warning。
