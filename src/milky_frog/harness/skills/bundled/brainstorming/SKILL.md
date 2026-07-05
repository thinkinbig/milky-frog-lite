---
name: brainstorming
description: Use when starting any new feature, project, or non-trivial task - requires presenting a design and getting approval before writing any code or taking implementation action
---

# Brainstorming Ideas Into Designs

## Core Principle

Do NOT invoke any implementation skill, write any code, scaffold any project, or take any implementation action until you have presented a design and the user has approved it.

This applies universally, regardless of project complexity. There is no "this is too simple" exemption.

## The Nine-Step Process

1. **Explore context** — read relevant files, docs, existing code
2. **Ask clarifying questions** — one question per message, never a list
3. **Propose 2–3 approaches** — each with concrete trade-offs
4. **Present design sections** — seek approval after each section
5. **Scale to complexity** — a few sentences for simple items, up to 300 words for nuanced sections
6. **Write the spec** — save to `/tmp/milky-frog/specs/YYYY-MM-DD-<topic>-design.md`
7. **Self-review the spec** — check for placeholders, contradictions, ambiguity
8. **Request user review** — do not proceed until approved
9. **Invoke writing-plans** — the only implementation action that follows brainstorming

## Key Constraints

- **One question at a time** — never present a list of questions
- **No code before approval** — not even "just to illustrate"
- **Design covers:** architecture, components, data flow, error handling, testing
- **Terminal state** — brainstorming ends only when you invoke writing-plans after approval

## Anti-Pattern: "This Is Too Simple To Need A Design"

Even a todo list needs a design. Simple things become complex. The 5 minutes spent on a design prevents hours of rework.

If you catch yourself thinking "this is too simple," that thought means you should present the design even faster — it will take almost no time.

## Red Flags — STOP, You Are Skipping Brainstorming

- Writing code to "explore" before presenting a design
- Asking multiple questions in one message
- Saying "let me just start and we can adjust"
- Proposing a single approach without alternatives
- Treating approval of one section as approval to implement
