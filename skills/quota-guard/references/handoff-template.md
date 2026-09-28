# Quota handoff

<!-- Write this to .claude/quota-handoff.md in the project root before the limit hits.
     The next session starts with nothing but this file and the repository.
     Be concrete: paths, commands, exact error text. Delete the file when the task is done. -->

- **Written:** YYYY-MM-DD HH:MM
- **Reason:** 5h window at NN% (or: 7d window at NN%)
- **Resume at:** YYYY-MM-DD HH:MM
- **Resume mechanism:** background wait | /loop wakeup | resume_after_reset.sh (PID, log path) | manual

## Goal

One or two sentences: what the user asked for and what "done" means.

## Done

- [x] Finished and verified step, with the file paths it touched

## In progress

- State of the unit that was interrupted, if any. Say exactly what is half-done.
- Uncommitted changes: yes/no. Branch: `name`. Last commit: `sha subject`.

## Next steps

1. First concrete action for the next session
2. ...

## Verify with

```sh
# commands that prove the work is correct, e.g. tests, build, lint
```

## Open questions / decisions for the user

- ...

## Context the next session needs

- Non-obvious findings, dead ends already tried, relevant docs or links.
