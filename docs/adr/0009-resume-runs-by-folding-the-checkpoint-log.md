# Resume Runs by folding the Checkpoint log into a RunState

`Harness` keeps its glossary role — "the runtime coordinator that *advances* a Run" — and stays the stateful advancer. We extract the live transcript out of `Harness.run`'s locals into a frozen `RunState` value, and make a single reducer the only thing that grows it. Resuming a Run becomes replaying its append-only Checkpoint log through that same reducer. This is phase 1 of unblocking multi-turn / resume / steering (ADR-0001 named the linear loop; resume was left a stub).

## The problem

`Harness.run` is simultaneously the *seeder* of a transcript (`messages = [system, user]`, built fresh every call) and the *advancer* of it (the model→Tool loop). There is no entry point that advances a transcript it did not itself construct, so `resume` cannot exist without rewriting `run()`. The transcript also lives as loose locals maintained *in parallel* with the Checkpoint event log — two representations of the same history, updated by two code paths.

The fix is not to extract "the loop" into a free function. The loop *is* the Harness's one intrinsic job, so it stays a private `_advance` method. The thing that must become separable is the **state**.

## The decision

- **`RunState`** is a frozen `@dataclass(frozen=True, slots=True)` carrying `run_id`, `workspace`, `messages`, `completed_model_calls`, and `run_usage`. It is threaded through `_advance` (`state = state.append(...)` per turn) and returned, so model-call accounting and token totals continue across a resume instead of resetting. `RunState` is the in-memory mirror of the append-only log, so "never mutate prior" (ADR-0002) applies to it identically.

- **One reducer writes the transcript.** `reduce(state, event) -> RunState` is the *sole* function that grows a transcript. The live loop feeds it each event as it is emitted; resume feeds it the persisted events in a replay. `fold(events) == live` therefore holds **by construction** — there is no second transcript-builder to drift out of sync, and no reconciliation test to maintain. This mirrors how the existing event stream (`ModelMessageCompleted`, `ToolCallCompleted`) is already emitted into the Checkpoint; we now also fold it back into state.

- **A dangling tool call is repaired by appending a real event.** An append-only log can end mid-turn — interrupted after `ToolCallRequested` but before `ToolCallCompleted`. Naively folding that yields a transcript whose tail is an assistant message with an unsatisfied `tool_call`, which most providers reject. The reducer repairs it by appending a synthetic `is_error` `ToolCallCompleted` (an "interrupted" result), durably and append-only — not an in-memory-only patch. The model then sees the interruption and re-decides. We do **not** blindly re-execute the interrupted Tool: its side-effect status is unknown (a half-written file), so re-exec could corrupt the Workspace, consistent with ADR-0002's "interrupted Tool remains explicitly `unknown`."

- **`resume(run_id)` is the only new verb, and it takes no prompt.** Every resumable state — `PAUSED_LIMIT`, `CANCELLED`, interrupted-mid-turn — has *pending work* and needs *zero input* to advance. The only status that would require new input is `COMPLETED` (a clean assistant tail with nothing pending), and "add a new goal to a finished Run" is multi-turn chat, not resume — it is deferred to phase 2, where its input channel must exist anyway. So `COMPLETED` is simply not resumable, and `resume` carries no optional `prompt` parameter to serve an out-of-scope case. `FAILED` is not auto-resumed either: the failure cause usually recurs on a blind re-advance, so a human must address it (phase 2's new-input path), not `resume`.

- **Steering is wholly deferred to phase 2.** True mid-run steering needs a concurrent input channel that touches `runtime.py`'s synchronous boundary (a background reader, or async-ifying the foreground). Phase 1 does *not* pre-place a between-turn `steering_queue` poll in `_advance`: with no producer it would be dead code, so the poll and its producer land together in phase 2 rather than leaving an inert seam behind.

- **`CheckpointStore` gains a read path.** `SqliteCheckpointStore` is append/create-only today; resume requires `read_events(run_id)`. This is mechanical and does not change the append-only schema (ADR-0002).

## Consequences

`Harness` is no longer a stateless service reusable across unrelated Runs — it holds *this* conversation's `RunState`. That matches "one foreground task at a time," and the "already-processing" guard becomes load-bearing: `MilkyFrog` holds one Harness per live conversation.

The system prompt is **regenerated** from `system_prompt(workspace)` on resume rather than stored, so a change to the prompt template between the original Run and its resume yields a (slightly) different system message. This is an accepted decision, not an accident: the system prompt is regenerated on every Run anyway, and storing it would duplicate state the template already owns.

Because the reducer is the single writer, the previously parallel `messages` list disappears: the live loop and resume share one transcript-construction path, and a Checkpoint can be folded into a `RunState` at any boundary without special "rehydrated" handling. `resume(run_id)`, `run(...)`, and a future phase-2 new-input verb all reduce to the same shape — fold → seed → `_advance` — differing only in the seed.

This ADR is scoped to phase 1 (resumable `RunState`). Mid-run steering and adding new input to an existing Run are explicitly out of scope and will get their own ADR alongside the concurrent input channel.

---

# 通过折叠 Checkpoint 日志为 RunState 来恢复 Run

`Harness` 保留其术语定义中的角色——"推进 Run 的运行时协调器"——继续作为有状态的推进者。我们把实时对话记录从 `Harness.run` 的局部变量中抽离为一个不可变的 `RunState` 值，并让单一 reducer 成为唯一增长它的途径。恢复一个 Run 即用同一个 reducer 重放其仅追加的 Checkpoint 日志。这是解锁多轮 / 恢复 / steering 的第一阶段（ADR-0001 确立了线性循环，恢复当时留作 stub）。

## 问题

`Harness.run` 同时是对话记录的*播种者*（`messages = [system, user]`，每次调用都重新构造）与其*推进者*（model→Tool 循环）。没有任何入口能推进一段并非由它自己构造的对话记录，因此 `resume` 不重写 `run()` 就无法存在。对话记录还以局部变量形式*与* Checkpoint 事件日志*并行*维护——同一段历史的两种表示，由两条代码路径更新。

修复办法不是把"循环"抽成自由函数。循环*正是* Harness 唯一的固有职责，因此它保留为私有的 `_advance` 方法。必须变得可分离的是**状态**。

## 决定

- **`RunState`** 是一个不可变的 `@dataclass(frozen=True, slots=True)`，承载 `run_id`、`workspace`、`messages`、`completed_model_calls`、`run_usage`。它被穿过 `_advance` 传递（每轮 `state = state.append(...)`）并返回，因此模型调用计数与 token 总量在恢复后继续累计而非清零。`RunState` 是仅追加日志的内存镜像，"绝不修改既往"（ADR-0002）对它同样适用。

- **单一 reducer 书写对话记录。** `reduce(state, event) -> RunState` 是*唯一*增长对话记录的函数。实时循环在每个事件产生时喂给它；恢复则以重放方式喂给它已持久化的事件。`fold(events) == live` 因此**天然成立**——不存在第二个会漂移失同步的对话记录构造者，也无需维护对账测试。这与既有事件流（`ModelMessageCompleted`、`ToolCallCompleted`）已写入 Checkpoint 的方式一致；我们现在也把它折叠回状态。

- **悬空的 tool call 通过追加真实事件来修复。** 仅追加日志可能在一轮中途结束——在 `ToolCallRequested` 之后、`ToolCallCompleted` 之前被中断。直接折叠会得到一段尾部为"带未满足 tool_call 的 assistant 消息"的对话记录，多数 provider 会拒绝。reducer 通过追加一个合成的 `is_error` `ToolCallCompleted`（"被中断"的结果）来修复，持久且仅追加——而非仅内存补丁。模型随后看到中断并重新决策。我们**不**盲目重跑被中断的 Tool：其副作用状态未知（可能半写入了文件），重跑可能损坏 Workspace，与 ADR-0002 "被中断的 Tool 明确保持 `unknown`" 一致。

- **`resume(run_id)` 是唯一的新动词，且不接收 prompt。** 每个可恢复状态——`PAUSED_LIMIT`、`CANCELLED`、轮内中断——都有*待办工作*且需要*零输入*即可推进。唯一需要新输入的状态是 `COMPLETED`（干净的 assistant 尾部、无待办），而"给已完成的 Run 添加新目标"是多轮对话，不是恢复——推迟到第二阶段，那时其输入通道本就必须存在。因此 `COMPLETED` 直接不可恢复，`resume` 也不携带为越界场景服务的可选 `prompt` 参数。`FAILED` 同样不自动恢复：失败原因在盲目重推时通常会复现，须由人来处理（第二阶段的新输入路径），而非 `resume`。

- **steering 完全推迟到第二阶段。** 真正的运行中 steering 需要一个触及 `runtime.py` 同步边界的并发输入通道（后台读取器，或将前台异步化）。第一阶段*不*在 `_advance` 里预先放置每轮之间的 `steering_queue` 轮询：没有生产者它就是死代码，因此轮询与其生产者在第二阶段一同落地，而非留下一个不生效的 seam。

- **`CheckpointStore` 增加读取路径。** `SqliteCheckpointStore` 目前仅追加/创建；恢复需要 `read_events(run_id)`。这是机械改动，不改变仅追加 schema（ADR-0002）。

## 影响

`Harness` 不再是可跨无关 Run 复用的无状态服务——它持有*当前*这段对话的 `RunState`。这符合"一次只运行一个前台任务"，且"正在处理中"的保护成为关键：`MilkyFrog` 为每段实时对话持有一个 Harness。

系统提示词在恢复时由 `system_prompt(workspace)` **重新生成**而非存储，因此原始 Run 与其恢复之间若模板有变，会产生（略有不同的）系统消息。这是被接受的决定而非意外：系统提示词本就在每个 Run 重新生成，存储它会重复模板已拥有的状态。

由于 reducer 是单一书写者，此前并行的 `messages` 列表消失：实时循环与恢复共享同一条对话记录构造路径，且 Checkpoint 可在任意边界折叠为 `RunState` 而无需特殊的"已重建"处理。`resume(run_id)`、`run(...)` 以及未来第二阶段的新输入动词都归约为同一形状——fold → 播种 → `_advance`——仅在播种环节不同。

本 ADR 范围限于第一阶段（可恢复的 `RunState`）。运行中 steering 与向既有 Run 添加新输入明确不在范围内，将与并发输入通道一起另立 ADR。
