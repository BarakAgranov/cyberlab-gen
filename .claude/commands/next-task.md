---
description: Start the next task in the current phase
---
Read `dev/phase-0-execution-log.md` to see which task was last completed. If the log doesn't exist yet, the next task is Task 0.

Then read the next task's section in `dev/phase-briefs/phase-0-agent-brief.md`. Read the task's `Required reading` in full — both Primary and Cross-references.

Confirm your understanding before writing any code:
- Which task you're about to start.
- The task's exit criteria, listed explicitly.
- The task's `Decision discretion` items (where you can choose) and `No discretion on` items (where you cannot).
- Any inputs from prior tasks the current task depends on.

Then propose a plan for the task. The plan should name the files you'll create or modify, the tests you'll write, and the order of operations. Stop and surface the plan to me. Do not write code until I approve the plan.

Once approved, execute the task. Follow the brief's `Work` section in order. When you hit a question the brief doesn't answer, check the `Required reading`. If it's still ambiguous, stop and record the question in `dev/decisions/NNNN-<slug>.md` per the authority gradient — do not resolve architectural ambiguities silently.

Do not skip ahead to later tasks. Do not implement Phase 1+ logic. Do not edit anything in `docs/`.