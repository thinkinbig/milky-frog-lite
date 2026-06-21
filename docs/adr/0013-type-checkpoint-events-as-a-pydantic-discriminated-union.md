# Type Checkpoint events as a Pydantic discriminated union

Checkpoint records were stored as `event_type: str` plus an untyped
`payload: dict[str, JsonValue]`. Factories and `reduce` duplicated the schema
as string literals and defensive `dict.get` parsing in `harness/events.py`.

## Decision

Model each durable event body as a frozen Pydantic `BaseModel` with a literal
`event_type` discriminator. Combine them into `CheckpointBody` and wrap stored
records in `RunEvent(body=..., sequence=..., ...)`.

- **Emit** — `harness/events.py` factories construct typed `RunEvent` values from domain objects.
- **Fold** — `harness/state.py` uses `match event.body` on concrete body types from `checkpoint/events.py`.
- **Persist** — SQLite still stores `event_type` + JSON `payload`; the adapter
  validates on read via `RunEvent.from_parts` and `load_checkpoint_body`.

Lifecycle signals (`handlers/events.py` / `BaseEvent`) stay separate: they are
ephemeral, in-process, and not replayed from the Checkpoint log.

## Consequences

- mypy can check event fields; typos in `event_type` fail at validation time.
- `RunEvent.payload` remains for CLI JSON export and backward-compatible tests
  (`RunEvent.from_parts`).
- New event types require a body model, a union member, a factory, and a
  `reduce` arm — the compiler/test suite surfaces omissions.

---

# 将 Checkpoint 事件类型化为 Pydantic discriminated union

Checkpoint 记录原先以 `event_type: str` 加无类型的 `payload: dict[str, JsonValue]` 存储。工厂与 `reduce` 在 `harness/events.py` 里用字符串字面量和防御性 `dict.get` 重复维护 schema。

## 决策

每种 durable 事件体建模为带 literal `event_type` 判别字段的 frozen Pydantic `BaseModel`，合并为 `CheckpointBody`，存储记录包在 `RunEvent(body=..., sequence=..., ...)` 中。

- **写入** — `harness/events.py` 工厂从领域对象构造类型化 `RunEvent`。
- **折叠** — `harness/state.py` 对 `checkpoint/events.py` 中的具体 body 类型做 `match event.body`。
- **持久化** — SQLite 仍存 `event_type` + JSON `payload`；适配器在读取时通过 `RunEvent.from_parts` 与 `load_checkpoint_body` 校验。

生命周期信号（`handlers/events.py` / `BaseEvent`）保持独立：临时的、进程内、不从 Checkpoint 日志 replay。

## 影响

- mypy 可检查事件字段；`event_type` 拼写错误在校验时失败。
- `RunEvent.payload` 保留，供 CLI JSON 导出与向后兼容测试（`RunEvent.from_parts`）。
- 新增事件类型需添加 body 模型、union 成员、工厂与 `reduce` 分支——编译器/测试会暴露遗漏。
