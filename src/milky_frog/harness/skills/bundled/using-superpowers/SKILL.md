---
name: using-superpowers
description: Use when starting any conversation - establishes how to find and use skills, requiring skill invocation before ANY response including clarifying questions
---

# Using Superpowers

## The Rule

**Invoke relevant or requested skills BEFORE any response or action** — including clarifying questions, exploring the codebase, or checking files.

Announce "Using [skill] to [purpose]" and follow the skill exactly.

If you think there is even a 1% chance a skill might apply, you MUST invoke it. This is not negotiable.

## Skill Priority

Process skills come first — they set the approach.

- "Let's build X" → **brainstorming** first, then implementation
- "Fix this bug" → **systematic-debugging** first, then domain skills
- "Implement a plan" → **executing-plans**
- "Review code" → **requesting-code-review**
- "Receiving feedback" → **receiving-code-review**

## Red Flags — You Are Rationalizing

| Thought | Reality |
|---------|---------|
| "This is just a simple question" | Questions are tasks. Check for skills. |
| "I need more context first" | Skill check comes BEFORE clarifying questions. |
| "Let me explore the codebase first" | Skills tell you HOW to explore. Check first. |
| "This doesn't need a formal skill" | If a skill exists, use it. |
| "I remember this skill" | Skills evolve. Read current version. |
| "This doesn't count as a task" | Action = task. Check for skills. |
| "The skill is overkill" | Simple things become complex. Use it. |
| "I'll just do this one thing first" | Check BEFORE doing anything. |

## User Instructions Take Precedence

User instructions (CLAUDE.md, direct requests) take precedence over skills, which override default behavior. Only skip skill workflows when the user has explicitly told you to.
