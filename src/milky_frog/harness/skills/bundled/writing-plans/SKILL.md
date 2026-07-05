---
name: writing-plans
description: Use when creating implementation plans for multi-step tasks before coding
---

# Writing Plans

## Overview

Create thorough implementation plans for multi-step tasks before coding, assuming the engineer has minimal codebase context. Document: files to modify, code samples, testing approach, relevant documentation, validation steps, and commit strategy. Structure as bite-sized tasks.

**Start announcement:** "I'm using the writing-plans skill to create the implementation plan."

**Save location:** `/tmp/milky-frog/plans/YYYY-MM-DD-<feature-name>.md`

## File Structure

Map files before defining tasks:

- Design focused units with clear boundaries and interfaces
- One responsibility per file; smaller focused files over monolithic ones
- Colocate files that change together; split by responsibility, not technical layers
- Follow established codebase patterns; restructuring only when truly necessary

## Task Right-Sizing

Smallest unit with own test cycle, worth independent review. Each task ends with testable output.

## Bite-Sized Granularity

Single action per step (2–5 minutes):
- Write failing test
- Verify failure
- Implement minimal code
- Verify pass
- Commit

## Plan Document Header

```markdown
# [Feature Name] Implementation Plan

**Goal:** [One sentence describing what this builds]

**Architecture:** [2–3 sentences about approach]

**Tech Stack:** [Key technologies/libraries]

## Global Constraints

[Project-wide requirements — version floors, naming rules, platform requirements]

---
```

## Task Structure

````markdown
### Task N: [Component Name]

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/exact/path/to/test.py`

**Interfaces:**
- Consumes: [dependencies from earlier tasks]
- Produces: [what later tasks depend on]

- [ ] **Step 1: Write the failing test**

[Complete test code block]

- [ ] **Step 2: Run test to verify it fails**

Run: `[exact command]`
Expected: [specific failure output]

- [ ] **Step 3: Write minimal implementation**

[Complete implementation code block]

- [ ] **Step 4: Run test to verify it passes**

Run: `[exact command]`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add [files]
git commit -m "[message]"
```
````

## No Placeholders

Every step contains actual needed content. Never write:
- "TBD", "TODO", "implement later"
- Generic instructions ("add error handling") without concrete code
- "Write tests for the above" without actual test code
- Undefined types, functions, or methods

## Critical Requirements

- Exact file paths always
- Complete code in every code step
- Exact commands with expected output
- DRY, YAGNI, TDD, frequent commits

## Self-Review Checklist

After completing plan:

1. **Spec coverage:** Find task implementing each requirement; list gaps
2. **Placeholder scan:** Search for red-flag patterns; fix them
3. **Type consistency:** Verify method names and signatures match across tasks

## Execution Handoff

After saving plan: "Plan complete and saved to `/tmp/milky-frog/plans/<filename>.md`. Ready to execute with the executing-plans skill."
