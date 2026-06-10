---
description: Start the next task in the current phase
---
First determine the **current phase** N: the highest N for which a phase brief exists — `dev/phase-N-agent-brief.md` (Phase 0's brief is `dev/phase-briefs/phase-0-agent-brief.md`). That brief is the plan of record for the phase.

Read `dev/phase-N-execution-log.md` to see which task was last completed. If that log doesn't exist yet, the next task is Task 0.

Then read the next task's section in the current phase's brief. Read the task's `Required reading` in full — both Primary and Cross-references.

If the next task is flagged as an **architect/maintainer** task (e.g. a doc-reconciliation task that edits `docs/`), it is the user's to do — surface it as such and offer to start the next *implementation* task instead, rather than executing it yourself.

Confirm your understanding before writing any code:
- Which task you're about to start.
- The task's exit criteria, listed explicitly.
- The task's `Decision discretion` items (where you can choose) and `No discretion on` items (where you cannot).
- Any inputs from prior tasks the current task depends on.

Then propose a plan for the task. The plan should name the files you'll create or modify, the tests you'll write, and the order of operations. Stop and surface the plan to me. Do not write code until I approve the plan.

Once approved, execute the task. Follow the brief's `Work` section in order. When you hit a question the brief doesn't answer, check the `Required reading`. If it's still ambiguous, stop and record the question in `dev/decisions/NNNN-<slug>.md` per the authority gradient — do not resolve architectural ambiguities silently.

Do not skip ahead to later tasks or later phases. Do not edit anything in `docs/` unless the task explicitly says to (some phases open with an architect/maintainer doc-reconciliation task that does).
