---
description: Finalize a task — verify, log, commit, tag
---
The current task is complete. Walk through these steps in order. For each, first check whether it's already been done; if so, skip it and say so. Don't redo work.

1. **Verify the build.** Run `just verify`. If it passes, note the result. If it doesn't, stop and surface the failures — don't proceed.

2. **Check the execution log.** Read `dev/phase-0-execution-log.md`. If the current task already has an entry, show it to me and ask if it needs updating. If not, draft one per the template at the bottom of `dev/phase-briefs/phase-0-agent-brief.md`. Surprises and friction must be specific — generic entries like "went smoothly" are not acceptable. Show me the entry before committing it.

3. **Check git status.** Run `git status` and `git log -3 --oneline`. If the task's changes are already committed, note it and skip step 4. Otherwise propose a commit message of the form "Phase 0 Task N: <short description>" and wait for my approval.

4. **Commit.** After I approve, commit. Include the execution log update in the same commit.

5. **Check for tag.** If the task's exit criteria specify a tag (e.g., `v0.0.1-setup` for Task 0), check whether the tag already exists with `git tag -l`. If yes, skip. If no, propose the tag name and wait for my confirmation.

Be explicit at each step about what you found and what you're doing. Don't assume anything is undone; don't assume anything is done. Check.