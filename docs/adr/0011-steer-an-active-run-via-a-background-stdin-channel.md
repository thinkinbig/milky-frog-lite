# Steer an active Run via a background stdin channel

> **Superseded.** Mid-run steering was removed as part of a lite simplification.
> The multi-turn interactive loop (ADR-0010) covers conversational interaction;
> steering — typing *while* a Run advances — added ~280 lines of threading and
> protocol machinery for marginal benefit in a "one task at a time" agent.
> The original ADR is preserved below for historical reference.

> **Persistence wording superseded by [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md).**
> (Historical: steering drained into user turns between model calls; each line
> was persisted via `append_user_message` + `RunEmitter.persist`.)

A user can now type a line *while* a Run is advancing; it is injected as the next
user turn at the following turn boundary. Input is read by a background thread
into a `SteeringChannel` the Harness drains between turns. This is phase 2b, the
last piece of the multi-turn/resume/steering arc (ADR-0009, ADR-0010).

## The constraint that shaped this

During `_advance` the main thread streams model output to stdout live. The
between-turn `prompt_in_box()` is a full `prompt_toolkit` Application that *owns*
the terminal, so it cannot coexist with concurrent streaming without a
full-screen output-pane + input-line layout — a **TUI**, which `CONTEXT.md`
forbids. So the question was not "thread vs async loop" but "how does a user
enter text mid-stream without a TUI." We chose the minimal mechanism: a
background **raw-line reader** (no input box during streaming). Its cost is that
typed characters echo into the streaming output; a hotkey-to-interject mechanism
that briefly opens the box is the future polish, deliberately deferred.

## The decision

- **`SteeringChannel` seam.** A `@runtime_checkable` `Protocol` with
  `drain() -> list[str]` (domain.py). `_advance` polls it; the Harness imports no
  threads and stays testable with a fake channel. `runtime_checkable` is required
  because `RunRequest.steering` is validated by pydantic (via `RunStarted`), which
  needs `isinstance` to work against the Protocol.

- **One queue, two drain points.** `_advance` drains the channel (a) at the top
  of each iteration — appending lines as user turns before the next
  model call (steering) — and (b) **instead of completing**: when the model
  returns no tool calls but a drain yields lines, it appends them and continues
  rather than finishing (follow-up). One unified queue covers both, which suits a
  single bounded foreground Run; pi needs two queues only because its `Agent` is a
  long-lived multi-prompt object. `max_model_calls` still bounds it — endless
  steering simply reaches `PAUSED_LIMIT`.

- **Steering lines are durable user turns.** Each drained line is persisted through
  `append_user_message` (ADR-0014), identical in effect to a 2a follow-up. A
  steered Run resumes from the same snapshot shape as any other Run.

- **Concurrency model A: a runtime-owned reader thread.** `_StdinSteering` runs a
  daemon thread that `select`s on stdin with a short timeout, reads ready lines,
  and queues non-blank ones. `_drive` starts it before `run_until_complete` and
  stops it after — alongside the existing SIGINT wiring — so the reader is live
  only for this Run. The main thread stays blocked in `run_until_complete`; only
  the producer is a thread. `Enter`-terminated line = steer; `Ctrl+C` = cancel
  (the cooperative-cancel path is untouched).

- **`select` for a clean stdin handoff.** A blocking `readline` would hold stdin
  until the next Enter, colliding with the between-turn `prompt_in_box()`. The
  `select` timeout lets the reader wake to check its stop flag and release stdin
  within ~0.1 s when the Run ends. The channel is enabled only on a POSIX TTY;
  elsewhere (Windows, pipes, tests) it is inert and steering is simply off.

## Consequences

Mid-Run steering works on POSIX TTYs and reuses the whole phase-1/2a engine —
a steering line is just a user turn from a different producer, persisted through
the same snapshot path as a 2a follow-up. The seam is also the inert-by-default
`steering_queue` poll that ADR-0009 declined to pre-place: it now exists *with*
a producer, not before one.

The accepted cost is echo-interleave: with the terminal in cooked mode, typed
characters appear amid the streaming output. This is the explicit trade of the
minimal mechanism; a hotkey-to-interject path (raw-mode keypress → pause stream →
flash the box → resume) is the polish and would get its own change. Steering is
unavailable on Windows and whenever stdin is not a TTY, with no effect on
non-steering behavior.

Because the reader thread is daemonized and `select`-based, it never blocks
process exit and reliably hands stdin back to the between-turn prompt. Lines that
arrive but are not drained before the Run ends are dropped on `stop`, so they
cannot leak into the next prompt.

This closes the multi-turn/resume/steering arc. Remaining steering polish (the
hotkey mechanism, observability of a resumed Run's status) is future work, not a
committed phase.

---

# 通过后台 stdin 通道引导一个活跃的 Run

> **持久化表述已被 [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md) 取代。**
> steering 仍在模型调用之间的轮次边界注入用户轮次；每行经 `append_user_message` +
> `RunEmitter.persist` 持久化，而非 `UserMessageAdded` 事件。

用户现在可以在一个 Run *推进期间*输入一行，它会在下一个轮次边界作为下一个用户轮次注
入。输入由后台线程读入一个 `SteeringChannel`，Harness 在轮次之间排空它。这是第二阶
段 b，是多轮/恢复/steering 这条线的最后一块（ADR-0009、ADR-0010）。

## 塑造本决定的约束

`_advance` 期间主线程实时把模型输出流式写到 stdout。轮次间的 `prompt_in_box()` 是一
个完整的 `prompt_toolkit` Application，*独占*终端，因此它无法与并发流式输出共存，除非
做成"输出窗格 + 输入行"的全屏布局——即 `CONTEXT.md` 禁止的 **TUI**。所以问题不是"线
程还是异步循环"，而是"用户如何在流式输出中途输入文本而不变成 TUI"。我们选择最小机制：
后台**原始行读取器**（流式期间无输入框）。其代价是输入的字符会回显进流式输出；让热键
短暂打开输入框的方案是未来打磨项，有意推迟。

## 决定

- **`SteeringChannel` seam。** 一个 `@runtime_checkable` `Protocol`，方法为
  `drain() -> list[str]`（domain.py）。`_advance` 轮询它；Harness 不引入线程，并可用
  假通道测试。`runtime_checkable` 是必需的，因为 `RunRequest.steering` 会被 pydantic
  校验（经 `RunStarted`），需要 `isinstance` 能对该 Protocol 工作。

- **一个队列，两个排空点。** `_advance` 在 (a) 每次迭代开头排空通道——把行作为
  用户轮次在下一次模型调用前追加进去（steering）——以及 (b) **在完成
  之前**排空：当模型返回无工具调用但排空得到行时，追加进去并继续而非结束（follow-up）。
  一个统一队列覆盖二者，契合单个有界的前台 Run；pi 需要两个队列仅因其 `Agent` 是长生
  命周期的多输入对象。`max_model_calls` 仍是上界——无尽 steering 只会到达
  `PAUSED_LIMIT`。

- **steering 行是持久的用户轮次。** 每个排空出的行经 `append_user_message` 持久化
  （ADR-0014），与 2a 的 follow-up 效果相同。被 steer 的 Run 与任何 Run 一样从同一快照形状恢复。

- **并发模型 A：runtime 持有的读取线程。** `_StdinSteering` 运行一个守护线程，对 stdin
  做带短超时的 `select`，读取就绪的行，并将非空行入队。`_drive` 在
  `run_until_complete` 之前启动它、之后停止它——与既有 SIGINT 接线并列——因此读取器仅
  在本 Run 期间存活。主线程仍阻塞在 `run_until_complete`；只有生产者是线程。
  `Enter` 结束的行 = steer；`Ctrl+C` = 取消（协作取消路径不变）。

- **用 `select` 实现干净的 stdin 交接。** 阻塞式 `readline` 会把 stdin 占到下一次
  Enter，与轮次间的 `prompt_in_box()` 冲突。`select` 超时让读取器醒来检查停止标志，并在
  Run 结束时约 0.1 秒内释放 stdin。该通道仅在 POSIX TTY 上启用；其他情况（Windows、管
  道、测试）下它无效，steering 即关闭。

## 影响

运行中 steering 在 POSIX TTY 上可用，并复用整套第一/2a 阶段引擎——一个 steering 行
只是来自不同生产者的用户轮次，经与 2a follow-up 相同的快照路径持久化。该 seam 也正是
ADR-0009 拒绝预先放置的、默认无效的 `steering_queue` 轮询：它如今*伴随*生产者存在，而非先于生产者。

被接受的代价是回显交错：终端处于 cooked 模式时，输入字符会出现在流式输出之间。这是最
小机制的明确取舍；热键打断路径（raw 模式按键 → 暂停流 → 闪现输入框 → 恢复）是打磨项，
会作为各自的改动。steering 在 Windows 及 stdin 非 TTY 时不可用，且不影响非 steering 行
为。

由于读取线程是守护线程且基于 `select`，它从不阻塞进程退出，并可靠地把 stdin 交还给轮
次间的 prompt。在 Run 结束前到达但未被排空的行会在 `stop` 时丢弃，因此不会泄漏到下一
个 prompt。

这关闭了多轮/恢复/steering 这条线。剩余的 steering 打磨（热键机制、被恢复 Run 状态的
可观测性）是未来工作，而非已承诺的阶段。
