# Use an append-only event log for checkpoints

> **Persistence mechanism superseded by [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md).**
> We no longer use an append-only `RunEvent` log or `run_events` table. The *goals*
> below — durable resume, completed Tools as facts, interrupted Tool stays
> `unknown` — are preserved via `RunState` snapshots; only the event-log *shape*
> is retired.

Checkpoint state will use versioned JSON events in a user-level SQLite database, with a `runs` projection for efficient status reads, rather than serializing runtime objects or overwriting opaque snapshots. Completed model and Tool events become durable facts, which makes resume behavior auditable and prevents already completed Tools from being executed again.

## Consequences

An interrupted Tool remains explicitly `unknown` and requires a user decision before retrying; Milky Frog does not claim exactly-once execution. ADR-0014: resume loads a snapshot and `seal()` appends a synthetic error tool message instead of re-executing. Snapshot `version` handles schema evolution; Python objects and provider SDK types never become the persistence format. See ADR-0013 (superseded) and ADR-0014 for current serialization.

---

# 使用仅追加事件日志实现 Checkpoint

> **持久机制已被 [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md) 取代。**
> 不再使用仅追加的 `RunEvent` 日志或 `run_events` 表。下文*目标*——durable resume、已完成 Tool 为事实、中断 Tool 保持 `unknown`——通过 `RunState` 快照保留；仅 event log 的*形态*退役。

Checkpoint 状态将使用用户级 SQLite 数据库中的版本化 JSON 事件，并通过 `runs` 投影高效读取状态，而不是序列化运行时对象或覆盖不透明快照。已完成的模型和 Tool 事件会成为 durable 事实，使恢复行为可审计，并防止已经完成的 Tool 被再次执行。

## 影响

被中断的 Tool 明确保持 `unknown` 状态，重试前必须由用户决定；Milky Frog 不承诺 exactly-once 执行。ADR-0014：加载快照并由 `seal()` 追加合成 error tool 消息。快照 `version` 负责演进；Python/SDK 类型不会成为 persistence 格式。见 ADR-0013（已取代）与 ADR-0014。
