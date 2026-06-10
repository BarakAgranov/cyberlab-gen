---
description: Finalize a task — verify, log, commit, tag
---
The current task is complete. First determine the **current phase** N (the highest N for which a phase brief exists — `dev/phase-N-agent-brief.md`, or `dev/phase-briefs/phase-0-agent-brief.md` for Phase 0). Then walk through these steps in order. For each, first check whether it's already been done; if so, skip it and say so. Don't redo work.

1. **Verify the build.** Run `just verify`. If it passes, note the result. If it doesn't, stop and surface the failures — don't proceed.

2. **Check the execution log.** Read `dev/phase-N-execution-log.md` (create it if this is the phase's first finished task). If the current task already has an entry, show it to me and ask if it needs updating. If not, draft one per the template at the bottom of the current phase's brief. Surprises and friction must be specific — generic entries like "went smoothly" are not acceptable. Show me the entry before committing it.

3. **Check git status.** Run `git status` and `git log -3 --oneline`. If the task's changes are already committed, note it and skip step 4. Otherwise propose a commit message in the repo's **conventional-commit** style — `type(scope): subject` (`feat`/`fix`/`refactor`/`docs`/`test`/`chore`), with an `(ADR NNNN)` suffix when a decision was recorded, e.g. `feat(schemas): add LabManifest skeleton models (ADR 00NN)`. Reference the phase/task in the body if useful. Wait for my approval.

4. **Commit.** After I approve, commit. Include the execution log update in the same commit.

5. **Check for tag.** If the task's exit criteria specify a tag, check whether it already exists with `git tag -l`. Tags are per-phase release tags at the phase-final task (e.g. `v0.3` at Phase 2 exit per `implementation-plan.md §1.7`; `v0.0.1-setup` was the Phase-0 form). If the tag exists, skip; if not, propose the tag name and wait for my confirmation.

Be explicit at each step about what you found and what you're doing. Don't assume anything is undone; don't assume anything is done. Check.
