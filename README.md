# quota-guard

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)

**Claude Code can see its own usage limits with this skill.** It reads the 5-hour and 7-day
windows while it works. It finishes or hands off cleanly before a limit hits, then resumes in the
next window.

Without it, Claude runs into the limit in the middle of an edit. The tree is half-changed, the plan
lives only in a dead context window, and you have to reconstruct everything by hand.

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

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Levels](#levels)
- [Resuming after a reset](#resuming-after-a-reset)
- [Configuration](#configuration)
- [Cost and privacy](#cost-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License](#license)

## Features

- **Reads real limits.** It uses the `rate_limits` data that Claude Code itself reports, not token estimates.
- **Four clear levels.** `OK`, `CAUTION`, `WRAP_UP` and `STOP`, each with a fixed set of actions for Claude.
- **Pace-aware.** It tracks the burn rate of the 5-hour window and warns early when a heavy session will
  empty it before the reset.
- **Clean handoffs.** Claude writes `.claude/quota-handoff.md` (goal, done, half-done state, next steps,
  verification commands) before the limit, while it still can.
- **Automatic resume.** It can wait in the background, use `/loop` wakeups, or start a detached
  `claude --continue` after the reset.
- **Proactive hooks.** The plugin warns Claude inside the conversation when the level changes, so it does
  not depend on Claude remembering to check.
- **No dependencies.** Python 3 standard library and Bash only.

## How it works

```
Claude Code status line ──stdin JSON──► cc-quota --capture ──► ~/.cache/cc-quota/usage.json
                                                                        │
headless / stale cache ──► cc-quota --probe (1 tiny Haiku call) ────────┤
                                                                        ▼
                     hooks (every prompt / tool call) ◄── quota_status.py ──► level + advice + resume time
                                   │                                         │
                                   ▼                                         ▼
                        "[quota-guard] WRAP_UP …"                SKILL.md tells Claude what to do
```

1. **`cc-quota`** caches the limits. Claude Code passes a `rate_limits` object to the status line
   command on every refresh. `cc-quota --capture` stores it. Where no status line runs (for example in
   `claude -p`), `cc-quota --probe` sends one minimal Haiku request and reads the `rate_limit_event`
   from the stream.
2. **`quota_status.py`** turns the snapshot into a decision: a level, the reasons, the burn rate, and
   the time when work can resume. It probes by itself when the cache is older than 15 minutes.
3. **`SKILL.md`** tells Claude when to check, how to act on each level, how to write the handoff, and
   how to resume.
4. **Hooks** (plugin install) run `quota_status.py --hook` after each prompt and tool call. They read
   only the cache, never probe, and speak only when the level is not `OK`. They repeat a level at most
   once every 10 minutes.

## Requirements

- [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) signed in with a Claude
  subscription (Pro or Max). API-key usage has no 5-hour or 7-day windows.
- Python 3.9 or newer on `PATH` as `python3`.
- macOS or Linux. Windows works through WSL.

## Installation

### Option A: plugin (recommended)

Run in Claude Code:

```
/plugin marketplace add sayedmahmod/claude-quota-guard
/plugin install quota-guard@quota-guard
```

This installs the skill, the `/quota` command, and the hooks. Then feed the status line data, see
[Status line](#status-line). Without that, the skill still works through probes.

### Option B: manual

```sh
git clone https://github.com/sayedmahmod/claude-quota-guard.git
cd claude-quota-guard
./install.sh --statusline
```

`install.sh` links the skill to `~/.claude/skills/quota-guard` and `cc-quota` to `~/.local/bin/cc-quota`.
With `--statusline` it also sets the status line in `~/.claude/settings.json` and writes a timestamped
backup first. It never overwrites existing files or an existing status line unless you pass `--force`.

| Flag | Effect |
|------|--------|
| `--statusline` | Set `cc-quota --capture` as the status line |
| `--copy` | Copy files instead of symlinking (use this if you delete the clone) |
| `--force` | Replace an existing skill, `cc-quota`, or status line |
| `--uninstall` | Remove the links this script created |

A manual install has no hooks. To add them, copy the `hooks` block from
[`hooks/hooks.json`](hooks/hooks.json) into `~/.claude/settings.json`. In both commands, replace
`"${CLAUDE_PLUGIN_ROOT}/skills/quota-guard/scripts/quota_status.py"` with
`~/.claude/skills/quota-guard/scripts/quota_status.py`.

### Status line

The status line gives fresh data for free on every refresh. Add this to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.local/bin/cc-quota --capture"
  }
}
```

With a plugin install, get the `cc-quota` binary with `./install.sh` from a clone, or copy
`skills/quota-guard/scripts/cc-quota` to any directory on your `PATH`.

**Do you already have a status line?** Keep it. Pipe the input through `cc-quota` first:

```sh
#!/usr/bin/env bash
input=$(cat)
printf '%s' "$input" | ~/.local/bin/cc-quota --capture >/dev/null
# ... your existing status line, reading from "$input"
```

### Verify

```sh
cc-quota                                                    # last cached snapshot
python3 ~/.claude/skills/quota-guard/scripts/quota_status.py
```

In Claude Code, run `/quota` (plugin install) or ask: *"What is my current quota?"*

## Usage

You do not need to do anything. Claude loads the skill for long or multi-step tasks, and when you
mention limits or quota. The skill tells Claude to check:

- at the start of a multi-step task,
- before expensive steps (large refactors, subagent fan-out, long builds),
- every 20 to 30 minutes of continuous work,
- whenever a `[quota-guard]` hook message appears.

To make it explicit, add a line to your project's `CLAUDE.md`:

```markdown
For long tasks, use the quota-guard skill and hand off before the usage limit.
```

### Commands

| Command | Purpose |
|---------|---------|
| `cc-quota` | Print the cached snapshot |
| `cc-quota --probe` | Refresh the cache with one tiny Haiku request |
| `cc-quota --capture` | Status line mode (reads JSON on stdin) |
| `quota_status.py` | Level, both windows, pace, advice, resume time |
| `quota_status.py --json` | The same as JSON |
| `quota_status.py --no-probe` | Never spend a request; use the cache only |
| `quota_status.py --wait` | Block until the limiting window resets, then report |
| `quota_status.py --hook` | Hook mode (used by `hooks/hooks.json`) |
| `resume_after_reset.sh` | Start a detached `claude --continue` after the reset |

## Levels

| Level | Trigger (defaults) | Claude's behavior |
|-------|--------------------|-------------------|
| `OK` | Below all thresholds | Works normally |
| `CAUTION` | 5h ≥ 60%, 7d ≥ 85%, or 5h empty within 60 min at current pace | Works leaner, checkpoints after each unit, confirms large tasks with you on a 7d warning |
| `WRAP_UP` | 5h ≥ 80%, or empty within 25 min | Finishes the current unit only, verifies, checkpoints, writes the handoff |
| `STOP` | 5h ≥ 92%, 7d ≥ 97%, or empty within 10 min | Writes the handoff now, tells you, schedules the resume |

A pace warning applies only when the window would run out **before** it resets. The resume time
uses the 7-day reset when the 7-day window is the binding limit.

## Resuming after a reset

The skill prefers the least intrusive option that works:

1. **Background wait (session stays open).** Claude runs `quota_status.py --wait` as a background
   task. When the window resets, the task exits, Claude wakes up, reads the handoff, and continues.
2. **`/loop` dynamic mode.** Claude schedules wakeups of up to one hour until the reset.
3. **Detached resume (session will close).** Only after it asks you first:

   ```sh
   skills/quota-guard/scripts/resume_after_reset.sh --dry-run -- --permission-mode acceptEdits
   skills/quota-guard/scripts/resume_after_reset.sh -- --permission-mode acceptEdits
   ```

   This waits with `nohup`, then runs `claude --continue -p "<resume prompt>"` in the project
   directory. Use `--session <id>` to resume a specific session and `--prompt` to change the
   prompt. It prints the PID (`kill <PID>` cancels) and the log path under `~/.cache/cc-quota/`.
4. **Manual.** Claude tells you the resume time. You say "continue" when it has passed.

> [!IMPORTANT]
> A headless `claude -p` run cannot answer permission prompts. Pass only the permissions the rest
> of the task needs after `--`, for example `--permission-mode acceptEdits` or
> `--allowedTools "Bash(npm test)"`. Do not use `--dangerously-skip-permissions` on a machine you
> care about.

### The handoff file

Claude writes `.claude/quota-handoff.md` from
[`references/handoff-template.md`](skills/quota-guard/references/handoff-template.md). The file
contains the goal, the finished steps, the exact half-done state, the numbered next steps,
verification commands, and open questions. The next session starts with this file and the
repository only. Claude deletes the file when the task is complete. You may want to add
`.claude/quota-handoff.md` to your `.gitignore`.

## Configuration

All thresholds are environment variables. Set them in your shell or in the `env` block of
`~/.claude/settings.json`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `QUOTA_GUARD_CAUTION` | `60` | 5h percent for `CAUTION` |
| `QUOTA_GUARD_WRAP_UP` | `80` | 5h percent for `WRAP_UP` |
| `QUOTA_GUARD_STOP` | `92` | 5h percent for `STOP` |
| `QUOTA_GUARD_WEEK_CAUTION` | `85` | 7d percent for `CAUTION` |
| `QUOTA_GUARD_WEEK_STOP` | `97` | 7d percent for `STOP` |
| `QUOTA_GUARD_MAX_AGE_MIN` | `15` | Cache age in minutes before `quota_status.py` probes |
| `CC_QUOTA_CACHE_DIR` | `~/.cache/cc-quota` | Location of the cache, history, and logs |
| `CC_QUOTA_BIN` | bundled script | Use a different `cc-quota` executable |

## Cost and privacy

- **Status line capture** is free. Claude Code already has the data.
- **A probe** sends one prompt (`reply only: ok`) to Haiku with `--max-turns 1`. That uses a very small
  part of your 5-hour window. Probes run only when the cache is older than 15 minutes, and never
  from hooks.
- **Everything stays local.** The cache (`usage.json`, mode `0600`), the pace history
  (`history.jsonl`), and the resume logs are in `~/.cache/cc-quota/`. Nothing is sent anywhere
  except the probe request to Anthropic through your own `claude` CLI.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `Level: UNKNOWN` | No snapshot yet. Configure the status line or run `cc-quota --probe`. |
| `Probe returned no rate_limit_event.` | The `claude` CLI is not on `PATH`, is signed in with an API key, or is too old. Run `claude --version` and `claude -p hi` by hand. |
| `Note: cached data is stale; probe skipped` | Expected with `--no-probe` or in hooks. Hooks stay silent on stale data. |
| Status line shows quota text instead of your own line | Chain the scripts as in [Status line](#status-line). |
| The hooks never say anything | Correct while the level is `OK`. Test with `QUOTA_GUARD_CAUTION=1`. |
| Resume did not start | Read the newest `~/.cache/cc-quota/resume-*.log`. Check that `claude` is on the `PATH` of a non-interactive shell. |

## Development

```sh
python3 -m unittest discover -s tests -v
bash -n install.sh skills/quota-guard/scripts/resume_after_reset.sh
```

Tests use a temporary `CC_QUOTA_CACHE_DIR` and never call the real `claude` CLI.

```
.
├── .claude-plugin/           plugin.json and marketplace.json
├── commands/quota.md         /quota slash command
├── hooks/hooks.json          UserPromptSubmit and PostToolUse hooks
├── skills/quota-guard/
│   ├── SKILL.md              instructions for Claude
│   ├── references/           handoff template
│   └── scripts/              cc-quota, quota_status.py, resume_after_reset.sh
├── tests/                    unittest suite
└── install.sh                manual installer
```

Contributions are welcome. Open an issue first for larger changes. Keep the scripts free of third-party
dependencies, and add a test for each behavior change.

## License

[MIT](LICENSE). This project is not affiliated with Anthropic.
