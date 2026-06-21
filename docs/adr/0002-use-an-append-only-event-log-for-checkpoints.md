# Use an append-only event log for checkpoints

Checkpoint state will use versioned JSON events in a user-level SQLite database, with a `runs` projection for efficient status reads, rather than serializing runtime objects or overwriting opaque snapshots. Completed model and Tool events become durable facts, which makes resume behavior auditable and prevents already completed Tools from being executed again.

## Consequences

An interrupted Tool remains explicitly `unknown` and requires a user decision before retrying; Milky Frog does not claim exactly-once execution. ADR-0009 builds on this: resume folds the event log into a `RunState` and repairs an interrupted Tool by appending a synthetic `is_error` result rather than re-executing it. Event schemas require migrations and backward-compatibility discipline, but Python objects and provider SDK types never become the persistence format. ADR-0013 types each persisted body as a Pydantic discriminated union (`CheckpointBody`); factories live in `harness/events.py`, models in `checkpoint/events.py`.

---

# 使用仅追加事件日志实现 Checkpoint

Checkpoint 状态将使用用户级 SQLite 数据库中的版本化 JSON 事件，并通过 `runs` 投影高效读取状态，而不是序列化运行时对象或覆盖不透明快照。已完成的模型和 Tool 事件会成为持久事实，使恢复行为可审计，并防止已经完成的 Tool 被再次执行。

## 影响

被中断的 Tool 明确保持 `unknown` 状态，重试前必须由用户决定；Milky Frog 不承诺 exactly-once 执行。ADR-0009 在此基础上展开：恢复时将事件日志折叠为 `RunState`，并通过追加一个合成的 `is_error` 结果来修复被中断的 Tool，而非重跑它。事件 schema 需要迁移和向后兼容约束，但 Python 对象和模型提供方 SDK 类型不会成为持久化格式。ADR-0013 将每种持久化 body 类型化为 Pydantic discriminated union（`CheckpointBody`）；工厂在 `harness/events.py`，模型在 `checkpoint/events.py`。
