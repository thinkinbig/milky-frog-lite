# Resume Runs by folding the Checkpoint log into a RunState

> **Checkpoint replay mechanism superseded by [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md).**
> `RunState`, `seal()`, resume validation, and the phase-1/2a resume *shape*
> (`load` → optional user turn → `_advance`) remain; `fold`/`reduce`, `RunEvent`
> seeds, and `read_events` do not.

`Harness` keeps its glossary role — "the runtime coordinator that *advances* a Run" — and stays the stateful advancer. We extract the live transcript out of `Harness.run`'s locals into a frozen `RunState` value and make **Harness mutators** the only thing that grows it. Resuming a Run loads that snapshot from SQLite and runs `seal()` before advancing. This is phase 1 of unblocking multi-turn / resume / steering (ADR-0001 named the linear loop; resume was left a stub).

## The problem

`Harness.run` is simultaneously the *seeder* of a transcript (`messages = [system, user]`, built fresh every call) and the *advancer* of it (the model→Tool loop). There is no entry point that advances a transcript it did not itself construct, so `resume` cannot exist without rewriting `run()`. The transcript also lived as loose locals maintained *in parallel* with a separate Checkpoint representation — two views of the same history, updated by two code paths (ADR-0014 collapses this to one persisted `RunState`).

The fix is not to extract "the loop" into a free function. The loop *is* the Harness's one intrinsic job, so it stays a private `_advance` method. The thing that must become separable is the **state**.

## The decision

- **`RunState`** is a frozen `@dataclass(frozen=True, slots=True)` carrying `run_id`, `workspace`, `messages`, `completed_model_calls`, and `run_usage`. It is threaded through `_advance` and returned, so model-call accounting and token totals continue across a resume instead of resetting. ~~`RunState` is the in-memory mirror of the append-only log~~ ADR-0014: `RunState` **is** the persisted Checkpoint snapshot (`runs.state_json`).

- ~~**One reducer writes the transcript.** `reduce(state, event) -> RunState` … `fold(events) == live`~~ **Harness mutators write the transcript** (`start_run`, `append_*` in `harness/state.py`); live loop and resume both operate on the same `RunState` shape — no replay.

- **A dangling tool call is repaired before the next advance.** ~~appending a synthetic `is_error` `ToolCallCompleted` event~~ `seal()` appends synthetic error **tool messages** into the snapshot (then persisted on resume). We do **not** blindly re-execute the interrupted Tool (ADR-0002).

- **`resume(run_id)` is the only new verb, and it takes no prompt.** Every resumable state — `PAUSED_LIMIT`, `CANCELLED`, interrupted-mid-turn — has *pending work* and needs *zero input* to advance. The only status that would require new input is `COMPLETED` (a clean assistant tail with nothing pending), and "add a new goal to a finished Run" is multi-turn chat, not resume — it is deferred to phase 2, where its input channel must exist anyway. So `COMPLETED` is simply not resumable, and `resume` carries no optional `prompt` parameter to serve an out-of-scope case. `FAILED` is not auto-resumed either: the failure cause usually recurs on a blind re-advance, so a human must address it (phase 2's new-input path), not `resume`.

- **Steering is wholly deferred to phase 2.** True mid-run steering needs a concurrent input channel that touches `runtime.py`'s synchronous boundary (a background reader, or async-ifying the foreground). Phase 1 does *not* pre-place a between-turn `steering_queue` poll in `_advance`: with no producer it would be dead code, so the poll and its producer land together in phase 2 rather than leaving an inert seam behind.

- **`CheckpointStore` gains a read path.** ~~`read_events(run_id)`~~ ADR-0014: `load_state(run_id)`; `prepare_resume` CAS-writes the resume snapshot.

## Consequences

`Harness` is no longer a stateless service reusable across unrelated Runs — it holds *this* conversation's `RunState`. That matches "one foreground task at a time," and the "already-processing" guard becomes load-bearing: `MilkyFrog` holds one Harness per live conversation.

The system prompt is **regenerated** from `system_prompt(workspace)` on resume rather than stored, so a change to the prompt template between the original Run and its resume yields a (slightly) different system message. This is an accepted decision, not an accident: the system prompt is regenerated on every Run anyway, and storing it would duplicate state the template already owns.

Because mutators are the single writer, the live loop and resume share one transcript-construction path. ~~`resume(run_id)` … fold → seed → `_advance`~~ `resume(run_id)` / `run(...)` / prompt continuation: **load → seal → (optional user turn) → `_advance`** (ADR-0010).

This ADR is scoped to phase 1 (resumable `RunState`). Mid-run steering and adding new input to an existing Run are explicitly out of scope and will get their own ADR alongside the concurrent input channel.

---

# 通过折叠 Checkpoint 日志为 RunState 来恢复 Run

> **Checkpoint replay 机制已被 [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md) 取代。**
> `RunState`、`seal()`、resume 校验与 load→advance 形状保留；`fold`/`reduce` 与
> `RunEvent` 不再使用。

`Harness` 保留其术语定义中的角色——"推进 Run 的运行时协调器"——继续作为有状态的推进者。我们把实时对话记录从 `Harness.run` 的局部变量中抽离为一个不可变的 `RunState` 值，并让 **Harness mutator** 成为唯一增长它的途径。恢复一个 Run 即从 SQLite load 该快照、在推进前运行 `seal()`。这是解锁多轮 / 恢复 / steering 的第一阶段（ADR-0001 确立了线性循环，恢复当时留作 stub）。

## 问题

`Harness.run` 同时是对话记录的*播种者*（`messages = [system, user]`，每次调用都重新构造）与其*推进者*（model→Tool 循环）。没有任何入口能推进一段并非由它自己构造的对话记录，因此 `resume` 不重写 `run()` 就无法存在。对话记录还曾以局部变量形式与 Checkpoint 的另一种表示*并行*维护——同一段历史的两种视图、两条代码路径（ADR-0014 将其收敛为单一持久化 `RunState`）。

修复办法不是把"循环"抽成自由函数。循环*正是* Harness 唯一的固有职责，因此它保留为私有的 `_advance` 方法。必须变得可分离的是**状态**。

## 决定

- **`RunState`** 是一个不可变的 `@dataclass(frozen=True, slots=True)`，承载 `run_id`、`workspace`、`messages`、`completed_model_calls`、`run_usage`。它被穿过 `_advance` 传递并返回，因此模型调用计数与 token 总量在恢复后继续累计而非清零。ADR-0014：`RunState` **即**持久化 Checkpoint 快照（`runs.state_json`）。

- **Harness mutator 书写对话记录。** `start_run`、`append_*`（`harness/state.py`）是*唯一*增长对话记录的途径；实时循环与恢复共用同一 `RunState` 形状——无需 replay。

- **悬空的 tool call 在下次推进前修复。** `seal()` 向快照追加合成的 error **tool 消息**（resume 时持久化）。我们**不**盲目重跑被中断的 Tool（ADR-0002）。

- **`resume(run_id)` 是唯一的新动词，且不接收 prompt。** 每个可恢复状态——`PAUSED_LIMIT`、`CANCELLED`、轮内中断——都有*待办工作*且需要*零输入*即可推进。唯一需要新输入的状态是 `COMPLETED`（干净的 assistant 尾部、无待办），而"给已完成的 Run 添加新目标"是多轮对话，不是恢复——推迟到第二阶段，那时其输入通道本就必须存在。因此 `COMPLETED` 直接不可恢复，`resume` 也不携带为越界场景服务的可选 `prompt` 参数。`FAILED` 同样不自动恢复：失败原因在盲目重推时通常会复现，须由人来处理（第二阶段的新输入路径），而非 `resume`。

- **steering 完全推迟到第二阶段。** 真正的运行中 steering 需要一个触及 `runtime.py` 同步边界的并发输入通道（后台读取器，或将前台异步化）。第一阶段*不*在 `_advance` 里预先放置每轮之间的 `steering_queue` 轮询：没有生产者它就是死代码，因此轮询与其生产者在第二阶段一同落地，而非留下一个不生效的 seam。

- **`CheckpointStore` 增加读取路径。** ADR-0014：`load_state(run_id)`；`prepare_resume` 以 CAS 写入 resume 快照。

## 影响

`Harness` 不再是可跨无关 Run 复用的无状态服务——它持有*当前*这段对话的 `RunState`。这符合"一次只运行一个前台任务"，且"正在处理中"的保护成为关键：`MilkyFrog` 为每段实时对话持有一个 Harness。

系统提示词在恢复时由 `system_prompt(workspace)` **重新生成**而非存储，因此原始 Run 与其恢复之间若模板有变，会产生（略有不同的）系统消息。这是被接受的决定而非意外：系统提示词本就在每个 Run 重新生成，存储它会重复模板已拥有的状态。

由于 mutator 是单一书写者，实时循环与恢复共享同一条对话记录构造路径。`resume(run_id)`、`run(...)` 以及第二阶段的新输入动词都归约为同一形状——**load → seal →（可选用户轮次）→ `_advance`**（ADR-0010）。

本 ADR 范围限于第一阶段（可恢复的 `RunState`）。运行中 steering 与向既有 Run 添加新输入明确不在范围内，将与并发输入通道一起另立 ADR。
