# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-09-28

### Added

- `quota-guard` skill: levels `OK`, `CAUTION`, `WRAP_UP` and `STOP` with fixed actions for Claude.
- `cc-quota`: cache the 5-hour and 7-day limits from the status line (`--capture`) or a probe (`--probe`).
- `quota_status.py`: level, burn rate, time to exhaustion, resume time, `--json`, `--wait` and `--hook` modes.
- `resume_after_reset.sh`: detached `claude --continue` after the reset.
- Handoff template, `/quota` command, plugin manifest with hooks, and `install.sh`.
