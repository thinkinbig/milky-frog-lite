# Handler lifecycle bus: notify plus bounded control returns

> Living doc — describes the current bus. The filename keeps its original
> `...read-only-lifecycle-bus` slug so existing links stay valid.

ADR-0004 (typed lifecycle Handlers; see [0004](0004-use-typed-event-handlers.md))
introduced two Handler channels — `observe` and `intercept` — on a shared
`HandlerRegistry`. The original `intercept` channel and its outcomes (`BlockTool`,
`TransformContext`, `PatchToolResult`) were a dead grab-bag: they let any handler
mutate execution arbitrarily through the same bus that carries **ephemeral
notifications**, while **persistent facts** live in the **Checkpoint snapshot**
(`runs.state_json`; see [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md)).
That open-ended channel was removed.

An earlier version of this decision went further — handlers returned **nothing**,
and policy/context build were deferred to "future explicit `Protocol` deps on
`Harness`" (a `ToolAuthorizer`, a `ContextBuilder`). That separate layer never
shipped, and it turned out to be unnecessary: the single event surface already
carries the payloads policy needs (`RunBeforeModel` holds the full `ModelRequest`),
and handler assembly is already centralized ([ADR-0015](0015-centralize-handler-assembly-in-default-handlers.md)).
A parallel policy-injection mechanism would have been redundant.

## Decision

One bus. `EventDispatcher` (`handlers/dispatcher.py`) is the only dispatch point;
**only `RunEmitter` publishes**, and handlers never publish. Most handlers are
pure observation and return `None`.

Policy and context build are expressed **on this bus**, as a closed, typed
`HandlerResult` union (`handlers/context.py`) that specific `RunBefore*` handlers
may return and that the **emitter** applies to the next step:

- `RunBeforeStart` → `SystemPromptSection` — additive context injection (e.g. `AgentContextHandler`).
- `RunBeforeModel` → carries the full `ModelRequest`; returns a reductive request rewrite (e.g. token budgeting). This is the per-call request-shaping seam.
- `RunBeforeTool` → `BlockResult` / `ApprovalResult` — authorization (`PolicyHandler`).

Four guardrails keep this from regressing into ADR-0004's open `intercept`:

1. Handlers still never publish signals — only observe and (at the points above) return.
2. Only an explicit, closed set of `RunBefore*` events accept returns; each has a typed result.
3. The **emitter**, not the handler, applies results — deterministically, in registration order.
4. `HandlerResult` variants and lifecycle signals are frozen dataclasses; `dispatch` is named `notify`.

There is **no** separate Harness-policy `Protocol` layer. Policy and context build
live here, on the lifecycle bus.

## Consequences

- The "Harness policy" lane is realized as `HandlerResult` control returns, not separate `Protocol` deps; CLAUDE.md and the ADR README describe it that way.
- ADR-0004's open-ended `intercept` is gone; its observe/priority model remains.
- Token budgeting will add the first **reductive** `RunBeforeModel` result variant plus its apply path in the emitter.
- The bus is "notify + bounded control returns," not "read-only" — the `notify` name is kept for the publish side only.
- Checkpoint snapshot typing is [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md), unaffected by this lane.

---

# Handler 生命周期总线：通知 + 受限的控制返回

> Living doc — 描述当前总线的实际行为。文件名保留原 `...read-only-lifecycle-bus`
> slug，以免现有链接失效。

[ADR-0004](0004-use-typed-event-handlers.md)（类型化 lifecycle Handler）在共享的
`HandlerRegistry` 上引入了两条通道——`observe` 与 `intercept`。最初的 `intercept`
通道及其 outcome（`BlockTool`、`TransformContext`、`PatchToolResult`）是个杂物抽象：
允许任意 handler 通过承载**临时通知**的同一条总线随意改写执行，而**持久化事实**存在于
Checkpoint 快照（`runs.state_json`；见
[ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md)）。这条开放通道已被移除。

本决策更早的一个版本走得更远——handler **不返回任何值**，policy/context build 留给
「未来注入 `Harness` 的显式 `Protocol` 依赖」（`ToolAuthorizer`、`ContextBuilder`）。
那一层始终没落地，事实证明也没必要：单一事件面本身就携带 policy 所需的载荷
（`RunBeforeModel` 持有完整 `ModelRequest`），handler 装配也已集中化
（[ADR-0015](0015-centralize-handler-assembly-in-default-handlers.md)）。再加一套平行的
policy 注入机制纯属冗余。

## 决策

一条总线。`EventDispatcher`（`handlers/dispatcher.py`）是唯一派发点；**只有
`RunEmitter` 发布**，handler 从不发布。多数 handler 是纯观察，返回 `None`。

policy 与 context build **就在这条总线上**表达——通过一个封闭、类型化的
`HandlerResult` 联合（`handlers/context.py`）：特定的 `RunBefore*` handler 可以返回它，
由 **emitter** 应用到下一步：

- `RunBeforeStart` → `SystemPromptSection`——加法式 context 注入（如 `AgentContextHandler`）。
- `RunBeforeModel` → 携带完整 `ModelRequest`；返回削减式请求改写（如 token budget）。这是 per-call 的请求塑造 seam。
- `RunBeforeTool` → `BlockResult` / `ApprovalResult`——授权（`PolicyHandler`）。

四条护栏防止它退回 ADR-0004 那种开放 `intercept`：

1. handler 仍然从不发布信号——只观察，并（在上述点）返回。
2. 只有一组显式、封闭的 `RunBefore*` 事件接受返回；每个都有类型化结果。
3. 应用结果的是 **emitter** 而非 handler——确定性地、按注册顺序。
4. `HandlerResult` 变体与生命周期信号都是 frozen dataclass；`dispatch` 命名为 `notify`。

**不存在**独立的 Harness-policy `Protocol` 层。policy 与 context build 就住在这条生命周期总线上。

## 影响

- 「Harness policy」lane 由 `HandlerResult` 控制返回实现，而非独立 `Protocol` 依赖；CLAUDE.md 与 ADR README 据此描述。
- ADR-0004 的开放式 `intercept` 已移除；其 observe/优先级模型保留。
- token budget 将引入第一个**削减式** `RunBeforeModel` 结果变体，以及 emitter 中的应用路径。
- 总线是「通知 + 受限控制返回」，不是「只读」——`notify` 之名仅指发布侧。
- Checkpoint 快照类型化见 [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md)，不受本 lane 影响。
