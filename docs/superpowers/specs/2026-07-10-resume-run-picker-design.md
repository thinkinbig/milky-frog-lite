# Resume Run Picker Design

## Scope

In the Textual UI, a bare `/resume` command opens a selectable list of recent
Runs in the current Workspace. Explicit Run IDs and prompts retain their
existing behavior.

## Interaction

- The picker lists current-Workspace Runs in checkpoint update order.
- Each row identifies the Run by short ID, status, update time, and a concise
  final-message summary.
- Up and down move the highlighted row; Enter selects it; Escape dismisses the
  picker and returns focus to the prompt.
- Selecting a Run follows the existing attach path with `advance_pending=True`.
  This preserves approval handling and immediately advances other selected Runs.
- An empty current-Workspace list shows the existing no-Runs error.

## Boundaries

- `RunController` exposes current-Workspace Run listing and retains ownership of
  attach outcomes.
- A dedicated Textual picker renders options and emits a typed selection
  message; it does not decide how to resume a Run.
- `TuiApp` opens the picker for bare `/resume` and forwards the selected ID to
  the existing attach-or-continue flow.

## Verification

Tests cover workspace filtering and picker selection/cancellation behavior,
then the repository's pytest, ruff check, ruff format check, and pyrefly check
all run before completion.
