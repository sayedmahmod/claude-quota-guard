"""Unit tests for quota_status.py and cc-quota. Run: python3 -m unittest discover -s tests"""
import datetime as dt
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quota-guard" / "scripts"


def load_module(cache_dir):
    os.environ["CC_QUOTA_CACHE_DIR"] = str(cache_dir)
    spec = importlib.util.spec_from_file_location("quota_status", SCRIPTS / "quota_status.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(five_used, week_used=10.0, age_min=1.0, five_reset_in=3600, week_reset_in=86400):
    now = time.time()
    return {
        "observed_at": dt.datetime.fromtimestamp(now - age_min * 60, dt.timezone.utc).isoformat(),
        "five_hour": {"used_percentage": five_used, "resets_at": int(now + five_reset_in)},
        "seven_day": {"used_percentage": week_used, "resets_at": int(now + week_reset_in)},
    }


class QuotaStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.qs = load_module(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def evaluate(self, snap, rows=()):
        return self.qs.evaluate(snap, time.time(), list(rows))

    def test_levels_follow_five_hour_thresholds(self):
        self.assertEqual(self.evaluate(snapshot(10))["level"], "OK")
        self.assertEqual(self.evaluate(snapshot(65))["level"], "CAUTION")
        self.assertEqual(self.evaluate(snapshot(85))["level"], "WRAP_UP")
        self.assertEqual(self.evaluate(snapshot(95))["level"], "STOP")

    def test_week_window_escalates(self):
        self.assertEqual(self.evaluate(snapshot(10, week_used=90))["level"], "CAUTION")
        status = self.evaluate(snapshot(10, week_used=99, week_reset_in=7200))
        self.assertEqual(status["level"], "STOP")
        # Resume waits for the 7d reset, not the earlier 5h reset.
        self.assertGreater(status["resume_in_sec"], 7000)

    def test_resume_only_for_wrap_up_and_stop(self):
        self.assertIsNone(self.evaluate(snapshot(65))["resume_in_sec"])
        self.assertIsNotNone(self.evaluate(snapshot(85))["resume_in_sec"])

    def test_burn_rate_escalates_before_threshold(self):
        now = time.time()
        rows = [{"t": now - 20 * 60, "used": 30.0}, {"t": now, "used": 50.0}]  # 1%/min, 50% left
        status = self.evaluate(snapshot(50, five_reset_in=4 * 3600), rows)
        self.assertEqual(status["level"], "CAUTION")
        self.assertEqual(status["minutes_to_exhaustion"], 50)

    def test_burn_rate_ignored_when_reset_comes_first(self):
        now = time.time()
        rows = [{"t": now - 20 * 60, "used": 30.0}, {"t": now, "used": 50.0}]
        status = self.evaluate(snapshot(50, five_reset_in=10 * 60), rows)
        self.assertEqual(status["level"], "OK")

    def test_unknown_without_data(self):
        self.assertEqual(self.qs.evaluate(None, time.time(), [])["level"], "UNKNOWN")

    def test_staleness(self):
        now = time.time()
        self.assertFalse(self.qs.is_stale(snapshot(10), now))
        self.assertTrue(self.qs.is_stale(snapshot(10, age_min=30), now))
        self.assertTrue(self.qs.is_stale(snapshot(10, five_reset_in=-60), now))
        self.assertTrue(self.qs.is_stale(None, now))

    def test_history_keeps_only_current_window(self):
        first = snapshot(20)
        self.assertEqual(len(self.qs.record_history(first)), 1)
        self.assertEqual(len(self.qs.record_history(first)), 1)  # same reading is not duplicated
        later = snapshot(25, age_min=0)
        later["five_hour"]["resets_at"] = first["five_hour"]["resets_at"]
        self.assertEqual(len(self.qs.record_history(later)), 2)
        other = snapshot(5, age_min=0, five_reset_in=18000)
        self.assertEqual(len(self.qs.record_history(other)), 1)

    def test_hook_mode_emits_once_per_level(self):
        (self.dir / "usage.json").write_text(json.dumps(snapshot(85)))
        out = self.run_hook()
        payload = json.loads(out)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("WRAP_UP", payload["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.run_hook(), "")

    def test_hook_mode_quiet_when_ok_or_stale(self):
        (self.dir / "usage.json").write_text(json.dumps(snapshot(10)))
        self.assertEqual(self.run_hook(), "")
        (self.dir / "usage.json").write_text(json.dumps(snapshot(95, age_min=60)))
        self.assertEqual(self.run_hook(), "")

    def run_hook(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "quota_status.py"), "--hook"],
            input=json.dumps({"hook_event_name": "PostToolUse"}),
            capture_output=True, text=True, check=True,
            env={**os.environ, "CC_QUOTA_CACHE_DIR": str(self.dir)},
        )
        return result.stdout.strip()


class CcQuotaTest(unittest.TestCase):
    def test_capture_writes_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_line = {"rate_limits": {
                "five_hour": {"used_percentage": 42.0, "resets_at": int(time.time()) + 3600},
                "seven_day": {"used_percentage": 7.0, "resets_at": int(time.time()) + 86400},
            }}
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "cc-quota"), "--capture"],
                input=json.dumps(status_line), capture_output=True, text=True, check=True,
                env={**os.environ, "CC_QUOTA_CACHE_DIR": tmp},
            )
            self.assertIn("5h: 42% used, 58% free", result.stdout)
            cached = json.loads((pathlib.Path(tmp) / "usage.json").read_text())
            self.assertEqual(cached["five_hour"]["used_percentage"], 42.0)


if __name__ == "__main__":
    unittest.main()
