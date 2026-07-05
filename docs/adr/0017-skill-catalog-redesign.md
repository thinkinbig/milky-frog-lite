# ADR-0017: SkillCatalog 三段式拆分 — discover / index / load

- **Status:** Proposed (2026-07-05)
- **Supersedes:** the single-class `SkillCatalog` that mixes three lifecycles
  behind `__init__` / `summaries()` / `prompt_locations()` / `load()`.
- **Related:** issue #77, commit `5a1c33f` (the perf fix this ADR makes
  structurally redundant).

> Note: `docs/adr/` was retired in favour of `docs/ARCHITECTURE.md` per the
> note in ADR-0013. We revive it for this one focused decision. If the team
> prefers, fold the rationale back into `ARCHITECTURE.md` and delete the file.

## Context

`CONTEXT.md` defines a Skill as "a **progressively loaded** instruction bundle"
— three tiers of disclosure:

| Tier | Loaded | What |
|------|--------|------|
| L1 | every system-prompt rebuild | `name` + `description` + path |
| L2 | on demand (model `read_file` or user `/skill`) | full SKILL.md body |
| L3 | as referenced | scripts / data bundled beside the skill |

The prompt-side contract is honoured: `<available_skills>` carries only
metadata, and `load(name)` is the on-demand body path. But the **Python
implementation collapses all three tiers into one class** (`SkillCatalog`)
and one shared state (`self._skills`), which forced the recent perf fix
(commit `5a1c33f`) to add a `_Discovered` cache type to repair a regression
caused by `_discover → _load` calling the full loader just to extract a
summary.

Four problems surface when reviewing the current shape against the
deep-module lens:

1. **`_discover` is implemented in terms of `_load`.** Discovery should be a
   pure `glob`; today it does a full frontmatter + instructions parse just
   to throw away `instructions`. This is the perf bug's **root cause**, not
   its symptom.

2. **The per-skill type is wrong-sized.** `SkillSummary` (L1 only) drops the
   path the catalog needs for `load()`; `Skill` (L1 + L2 + path) drags
   instructions into metadata storage. The recent `_Discovered` type was
   invented specifically as the cache record — it should not need to exist
   as a separate type if the design had chosen the right primary record.

3. **One class, three lifecycles.** `__init__` does discovery + indexing
   (one-time at startup); `summaries()` / `prompt_locations()` serve the
   index (per system-prompt rebuild); `load(name)` does fresh I/O (per
   user activation). All three mutate the same dict. ADR-0013 made the same
   observation about Handlers and split them; Skills gets the same
   treatment.

4. **Construction has hidden I/O.** `SkillCatalog(user, project)` reads
   files. There is no way to bind a catalog without doing I/O, which makes
   testing and lifecycle reasoning harder than it needs to be.

## Decision

### Three named operations

```
discover(dirs) -> tuple[Path, ...]    # glob, no read
index(paths) -> SkillIndex            # parse frontmatter, build frozen cache
SkillIndex.load(name) -> Skill        # read instructions on demand
```

The three operations have distinct costs (zero / one-time / per-call) and
distinct responsibilities (find / describe / read). Folding them together
is what produced the bug; naming them makes the cost shape explicit.

### One per-skill record

```python
@dataclass(frozen=True, slots=True)
class SkillRecord:
    """L1 metadata + path for one SKILL.md; immutable, cheap to hold."""
    summary: SkillSummary
    path: Path
```

`SkillRecord` replaces both `_Discovered` (the cache type) and the implicit
"L1-with-path" value the catalog was already needing. `Skill` (which carries
instructions) appears only at the L2 boundary — returned by `load(name)`.

### `SkillIndex` is the cache

```python
@dataclass(frozen=True, slots=True)
class SkillIndex:
    """Frozen metadata index built once at catalog construction."""
    records: dict[str, SkillRecord]   # name -> record

    def summaries(self) -> tuple[SkillSummary, ...]:
        return tuple(r.summary for r in self._sorted())

    def prompt_locations(self) -> tuple[tuple[str, str, Path], ...]:
        return tuple(
            (r.summary.name, r.summary.description, r.path)
            for r in self._sorted()
        )

    def load(self, name: str) -> Skill:
        try:
            record = self.records[name]
        except KeyError as error:
            raise KeyError(f"unknown skill: {name}") from error
        return _read_skill(record.path)
```

`summaries()` and `prompt_locations()` are pure functions of the frozen
index — no I/O, no caching concern, no `_Discovered`. `load(name)` reads
the file on demand. The "metadata is stable across the Run, body is fresh"
semantic emerges naturally from the types, not from a cache added on top
to undo an inversion.

### `SkillCatalog` becomes a thin facade

The existing public API is preserved:

```python
class SkillCatalog:
    """Discovers bundled, user, and project Skills.

    Priority (highest wins): project > user > bundled.
    """

    def __init__(self, user_directory: Path, project_directory: Path) -> None:
        paths: dict[str, Path] = {}
        for directory in (_BUNDLED_DIR, user_directory, project_directory):
            for path in discover(directory):
                paths[path.parent.name] = path   # priority merge: later wins
        self._index = index(paths)

    def summaries(self) -> tuple[SkillSummary, ...]:
        return self._index.summaries()

    def prompt_locations(self) -> tuple[tuple[str, str, Path], ...]:
        return self._index.prompt_locations()

    def load(self, name: str) -> Skill:
        return self._index.load(name)
```

The three-operation split lives in module-level functions; the class is a
binding + priority-merge convenience. Tests that build a catalog and call
the public methods continue to work without modification.

### Priority layering stays explicit

`discover` returns raw paths for one directory; the caller merges across
directories with project-wins ordering. We do **not** move priority into
`discover` itself — keeping `discover` pure-glob makes it easy to test and
to reason about.

### What changes vs what stays

| Concern | Before | After |
|---------|--------|-------|
| Per-skill value | `_Discovered(summary, path)` (cache record) | `SkillRecord(summary, path)` (the record) |
| `__init__` body | glob + `_load` for each file + cache | glob + `index(paths)` |
| `summaries()` cost | cache hit (after fix) | pure function of frozen index (naturally) |
| `prompt_locations()` cost | cache hit (after fix) | pure function of frozen index (naturally) |
| `load(name)` cost | one file read | one file read (unchanged) |
| Public API | `SkillCatalog(user, project)` + three methods | unchanged |
| Construction I/O | hidden in `__init__` | still in `__init__` (now via `index(paths)`), but explicit |
| `_discover` body | glob + read each file (calls `_load`) | glob only (`discover()`) |
| Frontmatter parsing failures | logged + skipped | logged + skipped (unchanged) |

## How to migrate

Step-by-step; each step is a committable increment.

1. **Add the new types** to `catalog.py`: `SkillRecord` and `SkillIndex`
   (both `frozen=True, slots=True`).

2. **Extract `discover(directory) -> tuple[Path, ...]`** as a module-level
   function: just the `glob` + per-file validation (skip missing files /
   non-directories). No file reads.

3. **Extract `index(paths) -> SkillIndex`** as a module-level function:
   for each path, read frontmatter and build a `SkillRecord`. Reuse the
   existing YAML parse code by splitting `_load(path) -> Skill` into
   `_parse_frontmatter(path) -> SkillSummary` and `_read_instructions(path,
   summary) -> Skill`. Malformed files log a warning and are skipped —
   matching today's behaviour.

4. **Add `SkillIndex.summaries() / prompt_locations() / load()`** as
   one-liners on the frozen record dict.

5. **Replace `SkillCatalog.__init__` body** with the priority-merge loop
   shown in the Decision section. `summaries()` / `prompt_locations()` /
   `load(name)` become one-line delegates to `self._index`.

6. **Delete `_Discovered`, `_sorted_entries`, the duplicated `sorted(self._paths.items())`
   in `summaries` and `prompt_locations`, and the `for skill in (self._load(path),)`
   trick in `prompt_locations`.**

7. **Run the test suite.** Existing tests should pass unchanged because
   the public API is preserved. The new `test_summaries_and_prompt_locations_serve_cached_metadata`
   test from commit `5a1c33f` stays as-is — it now proves the `SkillIndex`
   frozen invariant, not a cache field.

8. **Verify perf.** Build a micro-benchmark (or just count file reads via
   a wrapping test) for `prompt_locations()` × 1000 calls; the count must
   equal 1 (one read at `index()` time), not 1000.

## How to extend

**Add a new discovery source** (e.g. organization-scope skills in
`~/.milky-frog-org/skills`):

```python
catalog = SkillCatalog(org_dir, user_dir, project_dir)
```

Extend the tuple iterated in `__init__` with the new directory; the
priority-merge loop stays the same.

**Add a new skill metadata field** (e.g. `version` in frontmatter):

1. Add the field to `SkillSummary`.
2. Extend `_parse_frontmatter` to extract it.
3. Add the field to `<available_skills>` rendering in
   `format_skills_for_prompt` if callers should see it.
4. No changes to `SkillCatalog`, `SkillIndex`, or `SkillRecord` — the
   per-skill record automatically picks up the new metadata.

**Add L3 disclosure** (bundled scripts beside SKILL.md):

`load(name)` is the natural extension point — extend `_read_instructions`
to also resolve `path.parent / "scripts" / ...` references and return a
`Skill` whose `instructions` is the rendered template with resolved
references. `SkillIndex` and `SkillRecord` are untouched.

## Invariants

- `summaries()` and `prompt_locations()` never touch disk after `index()`
  runs.
- `load(name)` always reads the current file content (on demand).
- The set of skills visible to a Run is frozen at `SkillCatalog` construction.
- Frontmatter parsing failures are logged and the offending skill is
  skipped, not raised — preserves the current "robust to malformed files"
  behaviour.
- `_BUNDLED_DIR` is always included as the lowest-priority source.
- `SkillRecord.path` is always absolute (resolved at `index()` time).

## Consequences

- **The `_Discovered` patch type goes away.** The cache it represented is
  now the natural property of the `SkillIndex` type. Commit `5a1c33f`'s
  manual cache plumbing (`_Discovered`, `_sorted_entries`,
  `for skill in (self._load(path),)`) is replaced by a one-line
  `self._index = index(paths)`.

- **Performance wins structurally, not by patch.** The "don't re-read
  files on every model call" guarantee becomes a property of the
  abstraction rather than a discipline enforced by a hidden cache field.
  Future contributors cannot accidentally regress it by adding a code
  path that forgets to read from the cache.

- **Public API stable.** `SkillCatalog.__init__`, `summaries()`,
  `prompt_locations()`, and `load(name)` all keep their signatures and
  semantics. Tests, callers (`load_agent_context`,
  `_format_skill_injection`, `_handle_skill`), and the UI skill picker
  require no changes.

- **Three-tier progressive disclosure is honoured at every layer**, not
  just at the prompt boundary: types, operations, and runtime costs all
  reflect L1 (metadata, cheap) / L2 (body, on-demand) / L3 (resources,
  on-demand). The abstraction matches `CONTEXT.md`'s definition
  end-to-end.

- **Slight upfront cost.** The new file ships with one extra dataclass
  (`SkillRecord`), one extra frozen dataclass (`SkillIndex`), and two
  module-level functions (`discover`, `index`). We pay for this once; the
  saving is no `_Discovered`, no `_sorted_entries`, and no `_discover`
  body that does anything beyond `glob`.

## Rejected alternatives

- **Keep `_Discovered` and rename it to `SkillRecord`.** Solves the
  naming question but leaves `_discover` calling `_load` — the actual
  inversion stays. Half a fix.

- **Make `SkillCatalog.__init__` lazy (no I/O).** Pushes I/O into the
  first call to `summaries()` / `prompt_locations()`. Hides the
  construction cost behind a future surprise and complicates lifecycle
  reasoning. The current eager-at-construction model is honest; we keep
  it and make it explicit via the type.

- **Move priority merging into `discover`.** Couples `discover` to
  project-wins semantics and makes it harder to test independently.
  Keeping `discover` as a pure `glob` and merging in `__init__` matches
  the same separation ADR-0013 enforces for handler construction.

- **Drop `summaries()` / `prompt_locations()` from the public API and
  expose `SkillIndex` directly.** Cleaner abstraction but a breaking
  change for `ui/app.py` (`/skill` picker calls `catalog.summaries()`).
  Public API stability is preferred over a slightly cleaner boundary.

## See also

- `CONTEXT.md` — Skill definition (three-tier progressive disclosure).
- `docs/adr/0013-handler-design.md` — same "split by lifecycle" template
  applied to Handlers (construction / registration / lifetime).
- `src/milky_frog/harness/skills/catalog.py` — current implementation.
- `commit 5a1c33f` — the perf fix this ADR makes structurally redundant.
- Issue #77 — tracks the proposal and acceptance criteria.