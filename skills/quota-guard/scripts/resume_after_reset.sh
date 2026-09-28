#!/usr/bin/env bash
# Resume a Claude Code session once the limiting usage window has reset.
#
# Runs detached (nohup), so it survives the current session. It waits with
# quota_status.py --wait, then starts `claude -p` in the project directory with
# --continue (or --resume <id>) and a prompt that points at the handoff file.
#
# Usage:
#   resume_after_reset.sh [--dir PATH] [--session ID] [--prompt TEXT] [--dry-run] [-- CLAUDE_ARGS...]
#
# Headless `claude -p` cannot answer permission prompts. Pass the permissions the
# resumed run needs after `--`, for example:
#   resume_after_reset.sh -- --permission-mode acceptEdits --allowedTools "Bash(npm test)"
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dir="$PWD"
session=""
dry_run=0
foreground=0
prompt="Your usage limit has reset. Read .claude/quota-handoff.md, check the quota with the quota-guard skill, then continue the work from the next step listed there. Delete the handoff file when the task is complete."
extra=()

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) dir="$2"; shift 2 ;;
    --session) session="$2"; shift 2 ;;
    --prompt) prompt="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --foreground) foreground=1; shift ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; extra=("$@"); break ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

dir="$(cd "$dir" && pwd)"
cmd=(claude)
if [ -n "$session" ]; then cmd+=(--resume "$session"); else cmd+=(--continue); fi
cmd+=(-p "$prompt")
if [ ${#extra[@]} -gt 0 ]; then cmd+=("${extra[@]}"); fi

if [ "$dry_run" -eq 1 ]; then
  printf 'Directory: %s\nCommand:  ' "$dir"
  printf '%q ' "${cmd[@]}"
  printf '\n'
  exit 0
fi

if [ "$foreground" -eq 1 ]; then
  echo "[$(date '+%F %T')] waiting for quota reset"
  python3 "$here/quota_status.py" --wait
  echo "[$(date '+%F %T')] resuming in $dir"
  cd "$dir"
  exec "${cmd[@]}"
fi

log_dir="${CC_QUOTA_CACHE_DIR:-$HOME/.cache/cc-quota}"
mkdir -p "$log_dir"
log="$log_dir/resume-$(date +%Y%m%d-%H%M%S).log"
forward=(--foreground --dir "$dir" --prompt "$prompt")
if [ -n "$session" ]; then forward+=(--session "$session"); fi
if [ ${#extra[@]} -gt 0 ]; then forward+=(-- "${extra[@]}"); fi
nohup "$0" "${forward[@]}" >"$log" 2>&1 &
pid=$!
echo "Scheduled resume. PID: $pid"
echo "Log: $log"
echo "Cancel: kill $pid"
