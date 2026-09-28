#!/usr/bin/env python3
"""Turn cc-quota snapshots into a work decision for Claude.

Reads the cc-quota cache, refreshes it with a probe when it is stale, tracks the
burn rate of the 5-hour window, and prints one of four levels:

  OK       keep working normally
  CAUTION  work leaner, checkpoint more often
  WRAP_UP  finish the current unit, start nothing new, write the handoff
  STOP     write the handoff now and schedule the resume

Usage:
  quota_status.py                 human-readable status (probes if stale)
  quota_status.py --json          machine-readable status
  quota_status.py --no-probe      never spend a request on a probe
  quota_status.py --wait          block until the limiting window resets, then report
  quota_status.py --hook          Claude Code hook mode (reads hook JSON on stdin)
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
CACHE_DIR = pathlib.Path(os.environ.get("CC_QUOTA_CACHE_DIR", pathlib.Path.home() / ".cache" / "cc-quota"))
CACHE = CACHE_DIR / "usage.json"
HISTORY = CACHE_DIR / "history.jsonl"
HOOK_STATE = CACHE_DIR / "hook-state.json"

LEVELS = ["OK", "CAUTION", "WRAP_UP", "STOP"]
ADVICE = {
    "OK": "Work normally. Check again before the next large step.",
    "CAUTION": "Work leaner: avoid large reads and subagent fan-out, checkpoint after each finished unit.",
    "WRAP_UP": "Finish the current unit only. Start nothing new. Verify, checkpoint, write the handoff file.",
    "STOP": "Write the handoff file now, tell the user, and schedule the resume.",
    "UNKNOWN": "No quota data. Run `cc-quota --probe` or configure the status line.",
}


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


THRESHOLDS = {
    "caution": env_float("QUOTA_GUARD_CAUTION", 60),
    "wrap_up": env_float("QUOTA_GUARD_WRAP_UP", 80),
    "stop": env_float("QUOTA_GUARD_STOP", 92),
    "week_caution": env_float("QUOTA_GUARD_WEEK_CAUTION", 85),
    "week_stop": env_float("QUOTA_GUARD_WEEK_STOP", 97),
    "max_age_min": env_float("QUOTA_GUARD_MAX_AGE_MIN", 15),
}


def cc_quota_command():
    override = os.environ.get("CC_QUOTA_BIN")
    if override:
        return [override]
    bundled = HERE / "cc-quota"
    if bundled.exists():
        return [sys.executable, str(bundled)]
    found = shutil.which("cc-quota")
    return [found] if found else None


def load_snapshot():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return None


def age_minutes(snapshot, now):
    try:
        observed = dt.datetime.fromisoformat(snapshot["observed_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return max(0.0, (now - observed.timestamp()) / 60)


def is_stale(snapshot, now):
    if not snapshot:
        return True
    age = age_minutes(snapshot, now)
    if age is None or age > THRESHOLDS["max_age_min"]:
        return True
    # A window that already reset makes the cached percentage meaningless.
    for key in ("five_hour", "seven_day"):
        reset = (snapshot.get(key) or {}).get("resets_at")
        if reset and float(reset) <= now:
            return True
    return False


def run_probe():
    cmd = cc_quota_command()
    if not cmd:
        return "cc-quota not found"
    try:
        result = subprocess.run(cmd + ["--probe"], capture_output=True, text=True, timeout=150)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"probe failed: {exc}"
    if result.returncode != 0:
        return f"probe failed: {(result.stderr or result.stdout).strip() or result.returncode}"
    return None


def window(item, now):
    if not isinstance(item, dict) or item.get("used_percentage") is None:
        return None
    used = float(item["used_percentage"])
    reset = item.get("resets_at")
    reset_ts = float(reset) if reset else None
    return {
        "used": round(used, 1),
        "free": round(100 - used, 1),
        "resets_at": dt.datetime.fromtimestamp(reset_ts).astimezone().isoformat(timespec="minutes") if reset_ts else None,
        "resets_in_min": max(0, round((reset_ts - now) / 60)) if reset_ts else None,
        "reset_ts": reset_ts,
    }


def record_history(snapshot):
    """Append the 5h reading once per snapshot and return readings of the current window."""
    five = snapshot.get("five_hour") or {}
    if five.get("used_percentage") is None or not snapshot.get("observed_at"):
        return []
    entry = {
        "t": dt.datetime.fromisoformat(snapshot["observed_at"]).timestamp(),
        "used": float(five["used_percentage"]),
        "reset": five.get("resets_at"),
    }
    rows = []
    try:
        for line in HISTORY.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    if not rows or rows[-1].get("t") != entry["t"]:
        rows.append(entry)
        rows = rows[-500:]
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            HISTORY.write_text("".join(json.dumps(r) + "\n" for r in rows))
        except OSError:
            pass
    return [r for r in rows if r.get("reset") == entry["reset"]]


def burn_rate(rows, now, lookback_min=60):
    """Percent per minute over the recent part of the current window, or None."""
    recent = [r for r in rows if now - r["t"] <= lookback_min * 60]
    if len(recent) < 2:
        return None
    first, last = recent[0], recent[-1]
    minutes = (last["t"] - first["t"]) / 60
    if minutes < 5 or last["used"] <= first["used"]:
        return None
    return (last["used"] - first["used"]) / minutes


def evaluate(snapshot, now, rows):
    five = window(snapshot.get("five_hour"), now) if snapshot else None
    week = window(snapshot.get("seven_day"), now) if snapshot else None
    status = {
        "level": "UNKNOWN",
        "reasons": [],
        "five_hour": five,
        "seven_day": week,
        "burn_rate_pct_per_min": None,
        "minutes_to_exhaustion": None,
        "resume_at": None,
        "resume_in_sec": None,
    }
    if not five and not week:
        return status

    level = 0

    def bump(new, reason):
        nonlocal level
        level = max(level, LEVELS.index(new))
        status["reasons"].append(reason)

    if five:
        if five["used"] >= THRESHOLDS["stop"]:
            bump("STOP", f"5h window {five['used']:.0f}% used")
        elif five["used"] >= THRESHOLDS["wrap_up"]:
            bump("WRAP_UP", f"5h window {five['used']:.0f}% used")
        elif five["used"] >= THRESHOLDS["caution"]:
            bump("CAUTION", f"5h window {five['used']:.0f}% used")

        rate = burn_rate(rows, now)
        if rate:
            eta = five["free"] / rate
            status["burn_rate_pct_per_min"] = round(rate, 2)
            status["minutes_to_exhaustion"] = round(eta)
            if five["resets_in_min"] is None or eta < five["resets_in_min"]:
                if eta <= 10:
                    bump("STOP", f"5h window exhausted in ~{eta:.0f} min at current pace")
                elif eta <= 25:
                    bump("WRAP_UP", f"5h window exhausted in ~{eta:.0f} min at current pace")
                elif eta <= 60:
                    bump("CAUTION", f"5h window exhausted in ~{eta:.0f} min at current pace")

    if week:
        if week["used"] >= THRESHOLDS["week_stop"]:
            bump("STOP", f"7d window {week['used']:.0f}% used")
        elif week["used"] >= THRESHOLDS["week_caution"]:
            bump("CAUTION", f"7d window {week['used']:.0f}% used; confirm large tasks with the user")

    status["level"] = LEVELS[level]
    if level >= LEVELS.index("WRAP_UP"):
        week_blocks = week and week["used"] >= THRESHOLDS["week_stop"]
        binding = week if week_blocks else five
        if binding and binding["reset_ts"]:
            target = binding["reset_ts"] + 60
            status["resume_at"] = dt.datetime.fromtimestamp(target).astimezone().isoformat(timespec="minutes")
            status["resume_in_sec"] = max(0, int(target - now))
    return status


def fmt_duration(minutes):
    if minutes is None:
        return "?"
    minutes = int(minutes)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"


def fmt_local(iso):
    return dt.datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M") if iso else "unknown"


def render_text(status, age, source, note):
    lines = [f"Level: {status['level']}"]
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        w = status[key]
        if w:
            lines.append(f"{label}: {w['used']:.0f}% used ({w['free']:.0f}% free), "
                         f"resets {fmt_local(w['resets_at'])} (in {fmt_duration(w['resets_in_min'])})")
        else:
            lines.append(f"{label}: not available")
    if status["burn_rate_pct_per_min"]:
        lines.append(f"Pace: ~{status['burn_rate_pct_per_min']}%/min, "
                     f"5h window empty in ~{fmt_duration(status['minutes_to_exhaustion'])}")
    if status["reasons"]:
        lines.append("Why: " + "; ".join(status["reasons"]))
    if age is not None:
        lines.append(f"Data: {age:.0f} min old (source: {source})")
    if note:
        lines.append(f"Note: {note}")
    lines.append(f"Advice: {ADVICE[status['level']]}")
    if status["resume_at"]:
        lines.append(f"Resume at: {fmt_local(status['resume_at'])} (in {fmt_duration(status['resume_in_sec'] / 60)})")
    return "\n".join(lines)


def collect(allow_probe):
    now = time.time()
    snapshot = load_snapshot()
    source, note = "cache", None
    if is_stale(snapshot, now):
        if allow_probe:
            note = run_probe()
            if note is None:
                snapshot, source = load_snapshot(), "probe"
        else:
            note = "cached data is stale; probe skipped"
    rows = record_history(snapshot) if snapshot else []
    status = evaluate(snapshot, now, rows)
    age = age_minutes(snapshot, now) if snapshot else None
    status.update({"data_age_min": None if age is None else round(age, 1), "source": source, "note": note})
    return status, age, source, note


def hook_mode():
    """Emit context for Claude only when the level is not OK and it changed or 10 min passed."""
    try:
        event = json.load(sys.stdin).get("hook_event_name", "PostToolUse")
    except ValueError:
        event = "PostToolUse"
    status, age, source, note = collect(allow_probe=False)
    # Stale data may describe a window that already reset; stay quiet instead of misleading.
    if status["level"] in ("OK", "UNKNOWN") or note:
        return 0
    try:
        state = json.loads(HOOK_STATE.read_text())
    except (OSError, ValueError):
        state = {}
    now = time.time()
    if state.get("level") == status["level"] and now - state.get("t", 0) < 600:
        return 0
    try:
        HOOK_STATE.write_text(json.dumps({"level": status["level"], "t": now}))
    except OSError:
        pass
    text = "[quota-guard] " + render_text(status, age, source, note).replace("\n", " | ")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
    return 0


def wait_mode(args):
    for _ in range(args.max_cycles):
        status, age, source, note = collect(allow_probe=not args.no_probe)
        if status["resume_in_sec"] is None:
            break
        wake = status["resume_in_sec"] + args.buffer
        print(f"Waiting {fmt_duration(wake / 60)} until {fmt_local(status['resume_at'])} ...", flush=True)
        # After the reset the cached window is stale, so the next collect() probes.
        time.sleep(wake)
    status, age, source, note = collect(allow_probe=not args.no_probe)
    print(json.dumps(status, indent=2) if args.json else render_text(status, age, source, note))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument("--no-probe", action="store_true", help="never run cc-quota --probe")
    parser.add_argument("--wait", action="store_true", help="sleep until the limiting window resets")
    parser.add_argument("--buffer", type=int, default=120, help="extra seconds to wait after a reset (default 120)")
    parser.add_argument("--max-cycles", type=int, default=6, help="max wait cycles in --wait mode (default 6)")
    parser.add_argument("--hook", action="store_true", help="Claude Code hook mode")
    args = parser.parse_args()

    if args.hook:
        return hook_mode()
    if args.wait:
        return wait_mode(args)
    status, age, source, note = collect(allow_probe=not args.no_probe)
    print(json.dumps(status, indent=2) if args.json else render_text(status, age, source, note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
