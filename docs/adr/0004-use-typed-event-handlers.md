# Use typed event handlers

> **Partially superseded by [ADR-0012](0012-shrink-handler-registry-to-a-read-only-lifecycle-bus.md).**
> The `observe` channel, priority ordering, and `BaseHandler` bundles remain.
> The `intercept` channel and outcome types were removed.

Cross-cutting Harness behavior is implemented as typed lifecycle Handlers
registered on an instance-owned registry with explicit priority, rather than
`next()`-based middleware chains or global decorator state.

Handlers register on one channel:

- **`observe`** (and the `on` / `subscribe` aliases) — read-only inspection for
  live UI and observability; may not return outcomes or mutate signals.

Wildcard `subscribe` handlers participate in the same priority ordering as
type-specific observe handlers.

## Consequences

Decorators are registration syntax only; they register functions against
signals such as `BeforeTool`, `AfterTool`, and `RunFailed`. Ordering remains
visible and testable. Durable Run state is recorded separately as Checkpoint
events (`harness/events.py`), not through this bus.

---

# 使用类型化事件 Handler

> **部分已被 [ADR-0012](0012-shrink-handler-registry-to-a-read-only-lifecycle-bus.md) 取代。**
> `observe` 通道、优先级排序与 `BaseHandler` bundle 保留。
> `intercept` 通道及 outcome 类型已删除。

Harness 的横切行为通过类型化生命周期 Handler 实现。Handler 注册到实例持有的
registry，并具有显式优先级；不使用基于 `next()` 的 middleware 链或全局装饰器状态。

Handler 通过一条通道注册：

- **`observe`**（以及 `on` / `subscribe` 别名）——供实时 UI 与可观测性只读观察；
  不得返回 outcome 或修改信号。

wildcard `subscribe` Handler 与类型特定的 observe Handler 使用同一套 priority 排序。

## 影响

装饰器仅作为注册语法，将函数注册到 `BeforeTool`、`AfterTool` 和 `RunFailed`
等信号。执行顺序保持可见且可测试。可 durable 的 Run 状态通过 Checkpoint 事件
（`harness/events.py`）单独记录，不经过此总线。
