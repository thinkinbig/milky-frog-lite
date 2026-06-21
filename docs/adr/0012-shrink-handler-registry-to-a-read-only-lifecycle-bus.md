# Shrink HandlerRegistry to a read-only lifecycle bus

ADR-0004 introduced two Handler channels — `observe` and `intercept` — on a shared
`HandlerRegistry`. In production, every bundle (UI streaming, Langfuse) registers
only `observe` / `on` handlers; **zero** `intercept` handlers are wired. The
intercept channel and its outcomes (`BlockTool`, `TransformContext`,
`PatchToolResult`) were dead abstraction: they mixed **execution decisions** into
the same bus as **ephemeral notifications**, while **persistent facts** already
live in the append-only Checkpoint event log (`harness/events.py`).

## Decision

Keep three separate lanes:

1. **Checkpoint events** (`harness/events.py`) — durable, replayed on resume.
2. **Lifecycle signals** (`handlers/events.py`) — ephemeral; UI and observability only.
3. **Harness policy seams** (future) — explicit `Protocol` dependencies such as
   `ToolAuthorizer` or `ContextBuilder`, injected into `Harness` when a real need
   appears; not a pub/sub return-value channel.

`HandlerRegistry` is reduced to a read-only notification bus:

- Remove `intercept` and all intercept outcome types.
- Rename `dispatch` → `notify`; handlers may not return values or mutate signals.
- Make lifecycle signal models frozen.

Execution decisions that intercept once covered (block tool, transform context,
patch result) are **not** reimplemented yet — no production caller used them.
Add explicit seams when authorization, memory injection, or result sanitization
ship.

## Consequences

- UI and Langfuse bundles unchanged in spirit; they already used `on` only.
- `Harness._execute_tool` no longer branches on `BlockTool`.
- ADR-0004's intercept half is superseded; its observe/priority model remains.
- Checkpoint typing (discriminated union instead of string `event_type`) is a
  follow-up on `harness/events.py`, not part of this change.

---

# 将 HandlerRegistry 收缩为只读生命周期总线

ADR-0004 在共享的 `HandlerRegistry` 上引入了两条通道——`observe` 与 `intercept`。在生产环境中，所有 bundle（UI 流式输出、Langfuse）仅注册 `observe` / `on` handler；**零**个 `intercept` handler 被接入。intercept 通道及其 outcome（`BlockTool`、`TransformContext`、`PatchToolResult`）是死抽象：把**执行决策**与**临时通知**混在同一总线上，而**持久化事实**早已存在于 append-only Checkpoint 事件日志（`harness/events.py`）中。

## 决策

保留三条独立通道：

1. **Checkpoint 事件**（`harness/events.py`）——可 durable、resume 时 replay。
2. **生命周期信号**（`handlers/events.py`）——临时的；仅供 UI 与可观测性。
3. **Harness 策略 seam**（未来）——显式 `Protocol` 依赖，如 `ToolAuthorizer` 或 `ContextBuilder`，在真实需求出现时注入 `Harness`；而非带返回值的 pub/sub 通道。

`HandlerRegistry` 收缩为只读通知总线：

- 删除 `intercept` 及所有 intercept outcome 类型。
- `dispatch` 重命名为 `notify`；handler 不得返回值或修改信号。
- 生命周期信号模型改为 frozen。

intercept 曾覆盖的执行决策（拦截 tool、变换 context、修正 result）**暂不**重新实现——生产代码中无调用方。待授权、记忆注入或结果清洗落地时，再以显式 seam 添加。

## 影响

- UI 与 Langfuse bundle 语义不变；本就只用 `on`。
- `Harness._execute_tool` 不再对 `BlockTool` 分支。
- ADR-0004 的 intercept 部分被取代；其 observe/优先级模型保留。
- Checkpoint 类型化（以 discriminated union 替代字符串 `event_type`）是 `harness/events.py` 的后续工作，不在本次变更范围内。
