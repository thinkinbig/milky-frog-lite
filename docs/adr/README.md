# Architecture decision records

Milky Frog records significant trade-offs here. When code and an ADR disagree, **code
and [ADR-0014](0014-persist-checkpoints-as-runstate-snapshots.md) win** for
Checkpoint persistence unless a newer ADR says otherwise.

| ADR | Topic | Status |
|-----|-------|--------|
| [0001](0001-own-a-linear-agent-harness.md) | Linear Harness | Current |
| [0002](0002-use-an-append-only-event-log-for-checkpoints.md) | Append-only event log | **Goals kept; mechanism superseded by 0014** |
| [0003](0003-start-with-a-policy-based-local-sandbox.md) | Local Sandbox policy | Current |
| [0004](0004-use-typed-event-handlers.md) | Typed lifecycle Handlers | Current (intercept removed in 0012) |
| [0005](0005-keep-skills-declarative-and-non-executable.md) | Declarative Skills | Current |
| [0006](0006-separate-human-output-by-stream.md) | Stream output lanes | Current |
| [0007](0007-account-tokens-as-cumulative-and-context.md) | Token accounting | Current |
| [0008](0008-compose-handlers-via-providers-with-lifetime.md) | Handler bundles | Current |
| [0009](0009-resume-runs-by-folding-the-checkpoint-log.md) | Resume / `RunState` | **Partially superseded by 0014** (`seal`, validation kept) |
| [0010](0010-continue-a-run-with-a-new-user-turn.md) | Multi-turn `resume(prompt)` | Current (persistence wording in 0014) |
| [0011](0011-steer-an-active-run-via-a-background-stdin-channel.md) | Mid-run steering | **Superseded** (removed; lite simplification) |
| [0012](0012-shrink-handler-registry-to-a-read-only-lifecycle-bus.md) | Read-only Handler bus | Current |
| [0013](0013-type-checkpoint-events-as-a-pydantic-discriminated-union.md) | `CheckpointBody` union | **Superseded by 0014** |
| [0014](0014-persist-checkpoints-as-runstate-snapshots.md) | **`RunState` snapshot Checkpoint** | **Current source of truth for persistence** |

## Three lanes (post-0014)

1. **Checkpoint snapshot** — `runs.state_json`, `checkpoint/snapshot.py`
2. **Lifecycle signal** — `handlers/events.py`, notify-only bus (ADR-0012)
3. **Harness policy** — future explicit `Protocol` deps (not Handlers)

Do not call all three “Event” without a qualifier.
