# Use typed event handlers

Cross-cutting Harness behavior will be implemented as typed event Handlers registered on an instance-owned registry with explicit priority, rather than `next()`-based middleware chains or global decorator state. Authorization, checkpointing, and observability need deterministic lifecycle interception, but do not require an onion execution model.

## Consequences

Decorators are registration syntax only; they register functions against events such as `BeforeTool`, `AfterTool`, and `RunFailed`. Handlers may inspect or modify an event, reject an operation, or record state, while ordering remains visible and testable.

---

# 使用类型化事件 Handler

Harness 的横切行为将通过类型化事件 Handler 实现。Handler 注册到实例持有的 registry，并具有显式优先级；不使用基于 `next()` 的 middleware 链或全局装饰器状态。授权、checkpoint 和可观测性需要确定性的生命周期拦截，但不需要洋葱式执行模型。

## 影响

装饰器仅作为注册语法，将函数注册到 `BeforeTool`、`AfterTool` 和 `RunFailed` 等事件。Handler 可以检查或修改事件、拒绝操作或者记录状态，同时其执行顺序保持可见且可测试。
