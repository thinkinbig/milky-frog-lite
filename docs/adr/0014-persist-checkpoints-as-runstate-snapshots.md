# Persist Checkpoints as RunState snapshots

Milky Frog is a **lite** local agent. Checkpoint persistence no longer uses an append-only `RunEvent` log with ten typed `CheckpointBody` variants (ADR-0002, ADR-0009, ADR-0013). Each Run row stores a versioned **`RunState` JSON snapshot** plus a status projection and optional `final_message`.

## Supersedes (mechanism)

| ADR | What changes |
|-----|--------------|
| [0002](0002-use-an-append-only-event-log-for-checkpoints.md) | Append-only event log → snapshot; goals kept |
| [0009](0009-resume-runs-by-folding-the-checkpoint-log.md) | `fold`/`reduce`/`RunEvent` repair → `load`/`seal`/mutators; `RunState` + validation kept |
| [0013](0013-type-checkpoint-events-as-a-pydantic-discriminated-union.md) | Entire discriminated union removed |

**Still authoritative:** [0012](0012-shrink-handler-registry-to-a-read-only-lifecycle-bus.md) (lifecycle bus), [0010](0010-continue-a-run-with-a-new-user-turn.md) (multi-turn continuation). [0011](0011-steer-an-active-run-via-a-background-stdin-channel.md) is superseded (removed; lite simplification).

## The problem

Event-sourced Checkpoints gave us auditability and a single `fold`/`reduce` path, but the cost was high for a lite agent:

- Ten checkpoint event types, factories, and replay on every resume.
- Two representations of the same transcript (`RunEvent` log and in-memory `RunState`).
- Mental overhead for contributors (`ToolCallRequested` vs `ToolCallCompleted`, terminal events, …).

We still need durable resume, multi-turn continuation, steering, and safe recovery from interrupted tool calls — without the event-log machinery.

## The decision

- **`RunState` is the Checkpoint.** `CheckpointStore.save_state` / `load_state` persist messages, token accounting, and `reasoning_log` (one reasoning string per completed model call) as JSON via `checkpoint/snapshot.py`.
- **Harness mutators replace `fold`/`reduce`.** `harness/state.py` exposes `start_run`, `append_user_message`, `append_model_response`, `append_tool_result`, and `seal` (synthetic error tool results for interrupted calls).
- **`RunEmitter.persist`** writes snapshots at the same boundaries the old loop appended events (after each model/tool step, user turn, and terminal outcome). Lifecycle signals are unchanged (ADR-0012).
- **Schema:** `runs.state_json`, `runs.final_message`; **`run_events` table removed** (breaking change for existing local DBs).
- **`milky-frog show`** lists transcript summary instead of event sequences; `--json` exports the snapshot.

ADR-0002’s *goals* (durable resume, no blind re-execution of completed tools, interrupted-tool repair) are preserved. ADR-0009’s *mechanism* (replay through `reduce`) is superseded. ADR-0013’s discriminated union is superseded.

## Consequences

Resume is **O(1)** (one row read) instead of replaying the full log. We lose per-step audit timelines in SQLite; debugging uses the message transcript and lifecycle signals during live Runs.

Snapshot schema carries a `version` field for forward-compatible migrations without maintaining ten event body types.

---

# 以 RunState 快照持久 Checkpoint

Milky Frog 是 **lite** 本地 agent。Checkpoint 持久化不再使用带十种 `CheckpointBody` 的仅追加 `RunEvent` 日志（ADR-0002、ADR-0009、ADR-0013）。每个 Run 行存储版本化的 **`RunState` JSON 快照**，以及状态投影和可选的 `final_message`。

## 问题

事件溯源 Checkpoint 提供了可审计性和单一的 `fold`/`reduce` 路径，但对 lite agent 代价过高：

- 十种 checkpoint 事件类型、工厂，以及每次 resume 的全量 replay。
- 同一段 transcript 的两种表示（`RunEvent` 日志与内存 `RunState`）。
- 贡献者的心智负担（`ToolCallRequested` vs `ToolCallCompleted`、terminal event……）。

我们仍需要 durable resume、多轮延续、steering，以及从中断 tool call 安全恢复——但不需要 event log 机制。

## 决定

- **`RunState` 即 Checkpoint。** `CheckpointStore.save_state` / `load_state` 通过 `checkpoint/snapshot.py` 将 messages、token 统计和 `reasoning_log`（每轮 model 一条 reasoning）序列化为 JSON。
- **Harness mutator 替代 `fold`/`reduce`。** `harness/state.py` 提供 `start_run`、`append_user_message`、`append_model_response`、`append_tool_result` 和 `seal`（为中断 call 合成 error tool 结果）。
- **`RunEmitter.persist`** 在旧循环 append event 的相同边界写入快照（每步 model/tool、user turn、终端结果）。Lifecycle signal 不变（ADR-0012）。
- **Schema：** `runs.state_json`、`runs.final_message`；**删除 `run_events` 表**（对现有本地 DB 为 breaking change）。
- **`milky-frog show`** 展示 transcript 摘要而非 event 序列；`--json` 导出快照。

ADR-0002 的*目标*（durable resume、不重跑已完成 tool、中断 tool 修复）保留。ADR-0009 的*机制*（通过 `reduce` replay）被取代。ADR-0013 的 discriminated union 被取代。

## 影响

Resume 为 **O(1)**（读一行）而非 replay 全量日志。我们失去 SQLite 中的逐步审计时间线；调试依赖 message transcript 与 live Run 期间的 lifecycle signal。

快照 schema 带 `version` 字段，可在不维护十种 event body 的情况下向前兼容迁移。
