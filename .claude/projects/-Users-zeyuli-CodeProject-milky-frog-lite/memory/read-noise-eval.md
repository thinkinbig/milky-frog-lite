---
name: read-noise-eval
description: Read-noise evaluation harness — goal, scoring method, and the deferred-bench decision
metadata:
  type: project
---

Milky Frog reads too many irrelevant files when given a task (proven from a
Langfuse export: 9 reads per edit, runs reading 14–22 files, `你好` triggered 14
reads). The eval to reproduce/track this lives in `evals/` (collector, scoring,
runner, miner, review helper) with the problem write-up in
`docs/evals/read-noise-problem.md`.

Key decisions:
- **Primary metric = scope precision (method 3):** a read is noise if its path's
  *exact directory* isn't one the task's gold commit changed. Exact-dir, not
  subtree — a change in `ui/` must NOT make reading all of `ui/presenter/` look
  clean. Curated `also_in_scope` per task is the escape hatch for legit
  parent-package reads.
- **External public bench (SWE-bench etc.) is deferred**, not cancelled. The bug
  reproduces cheaply on Milky Frog's own repo; and the agent has no grep/search
  tool, so unfamiliar repos add a confound. Bring a small bench slice in later
  only as an overfitting/generalization check once a fix moves the metric.
- Dataset is mined from this repo's git commits (`evals/mine_change_tasks.py`);
  mined tasks are candidates that **require manual review** before use.

Measurement uses `ReadCollector` (subscribes `RunAfterTool` on the read-only
`LifecycleBus`) — in-process, no Langfuse round-trip. Langfuse stays the
long-term archive. See [[langfuse-log-storage]].
