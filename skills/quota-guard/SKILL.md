---
name: quota-guard
description: Read your own Claude Code usage limits (5-hour window and 7-day window) while you work, so you finish or hand off cleanly before hitting the limit and resume in the next window. Use at the start of any long or multi-step coding task, before large steps (big refactors, subagent fan-out, long test runs), every 20-30 minutes of sustained work, and whenever the user mentions quota, usage limits, rate limits, "5h limit", "weekly limit", or continuing after a reset.
---

# Quota Guard

You have a finite usage budget: a rolling **5-hour window** and a **7-day window**.
When a window hits 100%, your turns stop mid-task and unsaved context is lost.
This skill lets you read both windows, decide how to work, and hand off cleanly.

All paths below are relative to this skill's directory.

## 1. Check the quota

```sh
python3 scripts/quota_status.py
```

Example output:

```
Level: WRAP_UP
5h: 84% used (16% free), resets 2026-09-29 02:10 (in 3h 12m)
7d: 70% used (30% free), resets 2026-09-29 11:00 (in 12h 02m)
Pace: ~0.9%/min, 5h window empty in ~18m
Why: 5h window 84% used; 5h window exhausted in ~18 min at current pace
Data: 1 min old (source: cache)
Advice: Finish the current unit only. Start nothing new. Verify, checkpoint, write the handoff file.
Resume at: 2026-09-29 02:11 (in 3h 13m)
```

- Use `--json` when you need exact fields (`level`, `resume_in_sec`, `minutes_to_exhaustion`).
- Data comes from the status line cache. When it is older than 15 minutes, the script runs
  `cc-quota --probe`, which spends one tiny Haiku request. Do not call it in a tight loop.
- `Level: UNKNOWN` means no data. Tell the user once how to set it up (see README), then continue normally.

## 2. When to check

- At the start of a task that will take more than a few steps.
- Before each expensive step: large refactor, reading many files, spawning subagents, long builds.
- Every 20-30 minutes of continuous work, or after each finished unit of work.
- Immediately when a hook message starting with `[quota-guard]` appears.

## 3. Act on the level

| Level | Meaning | What you do |
|-------|---------|-------------|
| `OK` | Plenty left | Work normally. |
| `CAUTION` | 5h ≥ 60%, 7d ≥ 85%, or empty within 60 min at current pace | Work leaner: targeted reads instead of whole files, no subagent fan-out, checkpoint after each unit. On a 7d warning, tell the user and confirm before large new tasks. |
| `WRAP_UP` | 5h ≥ 80%, or empty within 25 min | Finish the current unit only. Start nothing new. Run the verification you have. Checkpoint. Write the handoff file. |
| `STOP` | 5h ≥ 92%, 7d ≥ 97%, or empty within 10 min | Write the handoff file now (section 4). Tell the user. Schedule the resume (section 5). End the turn. |

Rules:

- A "unit" is the smallest change that leaves the code working: one function, one file, one passing test.
  Never leave the tree broken at `WRAP_UP` or `STOP`. If the current unit cannot be finished, revert
  it or note the exact half-done state in the handoff.
- Checkpoint means: commit if the user allows commits in this project, otherwise leave changes staged
  and list them in the handoff. Never push, and never commit without permission.
- Write the handoff **before** the limit, not at it. At 100% you cannot write anything.
- The 5h window does not help when the 7d window is exhausted. `resume_at` already accounts for that.

## 4. Write the handoff

Write `.claude/quota-handoff.md` in the project root. Follow `references/handoff-template.md`.
Include: goal, done steps, the exact half-done state, next steps as a numbered list, verification
commands, open questions, and the resume time. The next session knows nothing else.

When you start a task and `.claude/quota-handoff.md` already exists, read it first and continue from
it. Delete it when the task is complete.

## 5. Resume in the next window

Pick the first option that fits. Always tell the user which one you chose and when work resumes.

1. **Session stays open (default).** Run this with the Bash tool's `run_in_background` option:
   ```sh
   python3 scripts/quota_status.py --wait
   ```
   It sleeps until the limiting window resets, re-reads the quota, and exits. Its exit wakes you up.
   Then read the handoff and continue.
2. **`/loop` dynamic mode is active.** Use `ScheduleWakeup` with `delaySeconds` set to
   `min(resume_in_sec, 3600)` and repeat until `resume_in_sec` is 0.
3. **Session will close, and the user agrees.** Start a detached headless resume:
   ```sh
   scripts/resume_after_reset.sh --dry-run -- --permission-mode acceptEdits
   scripts/resume_after_reset.sh -- --permission-mode acceptEdits
   ```
   Show the dry run first. This launches `claude -p --continue` later without supervision, so ask
   the user before running it and let them choose the permission flags. Report the PID and log path.
4. **None of the above.** Tell the user the resume time and that they should say "continue" then.

## 6. Do not

- Do not probe more than once per 15 minutes; the cached value is good enough.
- Do not stop early at `OK` or `CAUTION` because of the quota alone.
- Do not hide the quota state from the user when it changes your plan.
