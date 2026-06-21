# Continue a Run with a new user turn

> **Resume/persistence details superseded by [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md).**
> Optional `prompt`, status×prompt validation, and multi-turn interactive loop
> are unchanged. Follow-up turns are durable **user messages in the snapshot**,
> not `UserMessageAdded` Checkpoint events.

`resume` gains an optional `prompt`: `resume(run_id, prompt=None)`. Without a
prompt it picks up a Run's pending work (ADR-0009, unchanged). With a prompt it
appends the next user turn to an existing Run and advances — so a conversation
accumulates one transcript across prompts instead of starting a cold Run each
time. The interactive loop uses this to become genuinely multi-turn. This is
phase 2a; mid-run steering (typing *while* a Run advances) is still deferred to
phase 2b, which needs a concurrent input channel.

## The problem

The interactive loop started a **fresh Run every prompt**: `prompt_in_box()`
then `frog.run(task)`, which mints a new `run_id` and seeds `messages=[system,
user]` from scratch. The Agent therefore remembered nothing across turns — each
turn was a cold start. The capability users actually feel ("it forgot what I
just said") was missing, and it needs no concurrency at all: input arrives
strictly *between* Runs.

Phase 1 had deliberately collapsed `resume` to a no-prompt verb because, at that
scope, an optional `prompt` would only have served the then-out-of-scope
`COMPLETED`-with-input case — a dead parameter (ADR-0009). Phase 2a makes that
case the primary operation, so the parameter now earns its place and the
phase-1 decision flips on its changed premise rather than on taste.

## The decision

- **One verb, optional prompt.** `Harness.resume(run_id, *, max_model_calls,
  cancellation=None, prompt=None)` and `MilkyFrog.resume(run_id, prompt=None)`.
  No prompt → load, seal, advance pending work. With a prompt → load, seal,
  append the user turn, advance. We did not add a second verb: `continue` is a
  Python keyword, and a distinct method would duplicate ~95% of `resume`
  to carry one extra user line. The seam stays the phase-1
  shape — **load → seal → (optional user turn) → `_advance`** — with the prompt
  as one more in-memory (then persisted) user message.

- **The follow-up turn is durable.** A prompt is recorded via
  `append_user_message` and `RunEmitter.persist` into `runs.state_json`, not as
  a separate event type. The snapshot therefore reconstructs the full
  multi-turn transcript. The user turn is applied *after* `seal`, so a follow-up
  to an interrupted Run lands after that Run's repaired Tool result.

- **Validation by status × prompt.** Without a prompt, `PAUSED_LIMIT` /
  `CANCELLED` (pending work, no error) advance; `COMPLETED` / `FAILED` are
  rejected ("no pending work; provide a prompt"). With a prompt, any *terminal*
  Run advances — `COMPLETED`, `FAILED`, `PAUSED_LIMIT`, `CANCELLED` — since
  adding the next user message always gives the model something to do. A later
  crash-recovery refinement also permits `RUNNING` / `WAITING_*` rows only after
  acquiring their per-Run OS claim; a live foreground process keeps that claim,
  so concurrent advancement is rejected.

- **The interactive loop owns the conversation cursor.** `run_interactive` now
  takes `advance(task, run_id)` and holds a `run_id: str | None`: the first
  prompt starts a fresh Run, later prompts continue it (`result.run_id` threads
  forward, including after a cancel or pause), and `/clear` drops the cursor to
  begin a new conversation. The cursor lives in the loop — not in `MilkyFrog` —
  because it is purely an interactive concern: the one-shot `run` and `resume`
  CLI commands are stateless and would carry a meaningless cursor otherwise, and
  `MilkyFrog`'s role stays host boundary + assembly.

## Consequences

Interactive mode is now multi-turn: a session is one growing Run, resumable
later by `run_id` like any other. The CLI `resume` command is unchanged — it
still continues pending work with no prompt — so its phase-1 behavior is
preserved; the prompt-carrying path is reached only through the interactive
loop in this phase.

`FAILED` becoming continuable-with-a-prompt is intentional: the human supplies a
corrective turn, which is the safe way to address a failure (ADR-0009 declined
to *auto*-advance `FAILED` precisely because a blind re-advance tends to recur).

A resumed Run is projected as `RUNNING` before `_advance`. The status transition,
Tool-call repairs, and optional follow-up user event are committed in one SQLite
transaction after the Run's OS claim is acquired. A crash therefore cannot lose
the follow-up while leaving an active projection, and process exit releases the
claim so the row can be recovered safely.

Phase 2b — mid-run steering and its concurrent input channel — remains out of
scope and keeps its own future ADR. Phase 2a adds no between-turn queue poll to
`_advance`; with no producer it would be dead code (ADR-0009).

---

# 用新的用户轮次延续一个 Run

> **Resume/持久细节已被 [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md) 取代。**
> 可选 `prompt`、status×prompt 校验与多轮交互循环不变；follow-up 为快照中的**用户消息**，
> 而非 `UserMessageAdded` Checkpoint 事件。

`resume` 新增可选的 `prompt`：`resume(run_id, prompt=None)`。不带 prompt 时，它
接续一个 Run 的待办工作（ADR-0009，行为不变）。带 prompt 时，它向既有 Run 追加下
一个用户轮次并推进——于是一段对话在多次输入间累积为一份转录，而不是每次都冷启动一
个新 Run。交互循环借此真正变为多轮。这是第二阶段 a；运行中 steering（在 Run 推进
*期间*输入）仍推迟到第二阶段 b，那需要并发输入通道。

## 问题

交互循环**每次输入都启动一个全新 Run**：`prompt_in_box()` 之后 `frog.run(task)`，
后者铸造新的 `run_id` 并从零播种 `messages=[system, user]`。因此 Agent 跨轮次毫无
记忆——每一轮都是冷启动。用户真正能感受到的能力（"它忘了我刚说的话"）缺失了，而它
完全不需要并发：输入严格在 Run *之间*到达。

第一阶段曾有意将 `resume` 收敛为不带 prompt 的动词，因为在那个范围下，可选 `prompt`
只会服务于当时越界的 `COMPLETED`-带输入场景——一个死参数（ADR-0009）。第二阶段 a
使该场景成为主操作，于是该参数获得了存在意义，第一阶段的决定基于其前提的改变而翻转，
而非基于口味。

## 决定

- **一个动词，可选 prompt。** `Harness.resume(run_id, *, max_model_calls,
  cancellation=None, prompt=None)` 与 `MilkyFrog.resume(run_id, prompt=None)`。
  无 prompt → load、seal、推进待办工作。带 prompt → load、seal、追加用户轮次、推进。
  我们没有新增第二个动词：`continue` 是 Python 关键字，且独立方法会重复 `resume` 约 95% 的逻辑。
  seam 保持第一阶段形状——**load → seal →（可选用户轮次）→ `_advance`**。

- **后续轮次可 durable。** prompt 经 `append_user_message` 与 `RunEmitter.persist`
  写入 `runs.state_json`，而非 `UserMessageAdded` 事件。快照因此能重建完整多轮转录。
  用户轮次在 `seal` *之后*追加，因此 follow-up 落在已修复的 tool 结果之后。

- **按状态 × prompt 校验。** 无 prompt 时，`PAUSED_LIMIT` / `CANCELLED`（有待办、
  无错误）可推进；`COMPLETED` / `FAILED` 被拒（"无待办工作；请提供 prompt"）。带
  prompt 时，任何*终止态* Run 都可推进——`COMPLETED`、`FAILED`、`PAUSED_LIMIT`、
  `CANCELLED`——因为追加下一个用户消息总能给模型可做之事。后续的崩溃恢复收紧允许
  `RUNNING` / `WAITING_*` 行仅在取得该 Run 的 OS claim 后恢复；仍存活的前台进程会
  持有 claim，因此并发推进会被拒绝。

- **交互循环持有对话游标。** `run_interactive` 现在接收 `advance(task, run_id)` 并
  持有 `run_id: str | None`：首次输入启动新 Run，后续输入接续它（`result.run_id`
  向前传递，取消或暂停后亦然），`/clear` 丢弃游标以开始新对话。游标住在循环里——而非
  `MilkyFrog`——因为它纯属交互关注点：一次性的 `run` 与 `resume` CLI 命令是无状态的，
  否则会携带一个无意义的游标，而 `MilkyFrog` 的角色保持为宿主边界 + 组装。

## 影响

交互模式现在是多轮的：一次会话是一个不断增长的 Run，日后可像任何 Run 一样按 `run_id`
恢复。CLI `resume` 命令不变——仍以无 prompt 接续待办工作——其第一阶段行为得以保留；
带 prompt 的路径在本阶段仅经交互循环到达。

`FAILED` 变为可带 prompt 接续是有意为之：由人提供纠正性轮次，这是处理失败的安全方式
（ADR-0009 拒绝*自动*推进 `FAILED`，正因盲目重推往往复现）。

恢复后的 Run 会在 `_advance` 前投影为 `RUNNING`。状态转换、Tool-call 修复与可选的后续
用户事件会在取得 Run 的 OS claim 后，于同一个 SQLite 事务中提交。因此崩溃不会在留下
活跃投影的同时丢失 follow-up；进程退出又会释放 claim，使该行可被安全恢复。

第二阶段 b——运行中 steering 及其并发输入通道——仍在范围之外，保留其各自未来的 ADR。
第二阶段 a 不向 `_advance` 添加任何轮次间队列轮询；没有生产者它就是死代码（ADR-0009）。
