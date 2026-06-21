# Continue a Run with a new user turn

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
  No prompt → fold, seal, advance pending work. With a prompt → fold, seal,
  append the user turn, advance. We did not add a second verb: `continue` is a
  Python keyword, and a distinct method would duplicate ~95% of `resume`
  (fold→seed→advance) to carry one extra seed line. The seam stays the phase-1
  shape — **fold → seed → `_advance`** — with the prompt as one more seed.

- **The follow-up turn is a durable event.** A prompt is recorded as a new
  `UserMessageAdded` Checkpoint event, which `reduce` folds into a
  `Message(USER, …)`. The append-only log therefore reconstructs the full
  multi-turn transcript, and `reduce` stays the single transcript writer
  (ADR-0009). The user turn is appended *after* `seal`, so a follow-up to an
  interrupted Run lands after that Run's repaired Tool result.

- **Validation by status × prompt.** Without a prompt, only `PAUSED_LIMIT` /
  `CANCELLED` (pending work, no error) advance; `COMPLETED` / `FAILED` are
  rejected ("no pending work; provide a prompt"). With a prompt, any *terminal*
  Run advances — `COMPLETED`, `FAILED`, `PAUSED_LIMIT`, `CANCELLED` — since
  adding the next user message always gives the model something to do. An active
  Run (`RUNNING` / `WAITING_*`) is rejected either way: only one foreground Run
  advances at a time.

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

A resumed Run keeps its prior terminal status in the `runs` projection until
`_advance` writes a new terminal event, so a crash mid-continuation leaves the
status reading e.g. `COMPLETED` while new events exist after it. A re-resume
still folds correctly (the events are the source of truth); only the projection
lags. Tightening the projection to a `RUNNING` marker on entry is left for when
mid-run observability matters (phase 2b).

Phase 2b — mid-run steering and its concurrent input channel — remains out of
scope and keeps its own future ADR. Phase 2a adds no between-turn queue poll to
`_advance`; with no producer it would be dead code (ADR-0009).

---

# 用新的用户轮次延续一个 Run

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
  无 prompt → fold、seal、推进待办工作。带 prompt → fold、seal、追加用户轮次、推
  进。我们没有新增第二个动词：`continue` 是 Python 关键字，且独立方法会为多一行播种
  而重复 `resume` 约 95% 的逻辑（fold→seed→advance）。seam 保持第一阶段形状——
  **fold → seed → `_advance`**——prompt 只是多一个播种项。

- **后续轮次是持久事件。** prompt 被记录为新的 `UserMessageAdded` Checkpoint 事件，
  由 `reduce` 折叠为 `Message(USER, …)`。仅追加日志因此能重建完整的多轮转录，且
  `reduce` 仍是唯一的转录书写者（ADR-0009）。用户轮次在 `seal` *之后*追加，因此对一
  个被中断的 Run 的后续输入会落在该 Run 已修复的 Tool 结果之后。

- **按状态 × prompt 校验。** 无 prompt 时，仅 `PAUSED_LIMIT` / `CANCELLED`（有待办、
  无错误）可推进；`COMPLETED` / `FAILED` 被拒（"无待办工作；请提供 prompt"）。带
  prompt 时，任何*终止态* Run 都可推进——`COMPLETED`、`FAILED`、`PAUSED_LIMIT`、
  `CANCELLED`——因为追加下一个用户消息总能给模型可做之事。活跃 Run（`RUNNING` /
  `WAITING_*`）两种情况都被拒：一次只推进一个前台 Run。

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

被恢复的 Run 在 `runs` 投影中保留其先前终止状态，直到 `_advance` 写入新的终止事件，
因此续推中途崩溃会让状态读作如 `COMPLETED`，而其后已有新事件。再次恢复仍能正确折叠
（事件才是真相来源），仅投影滞后。把投影在入口收紧为 `RUNNING` 标记，留待运行中可观
测性变得重要时（第二阶段 b）。

第二阶段 b——运行中 steering 及其并发输入通道——仍在范围之外，保留其各自未来的 ADR。
第二阶段 a 不向 `_advance` 添加任何轮次间队列轮询；没有生产者它就是死代码（ADR-0009）。
