#!/usr/bin/env python3
"""Exercise the publisher freshness watchdog against recorded and synthetic states."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import watchdog_freshness as watchdog  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def at(text):
    return watchdog.parse_utc(text)


def ctas_status(last_update, **extra):
    generated = at(last_update)
    doc = {
        "schema_version": "ctas.public-status@1",
        "pipeline_status": "degraded",
        "origin": "local-snapshot",
        "last_successful_update": last_update,
        "export_checked_at": last_update,
        "valid_until": watchdog.iso(generated + dt.timedelta(minutes=30)),
        "latest_record_update": last_update,
        "candidate_count": 15687,
        "degraded_source_count": 6,
        "cadence": "about every 2 minutes",
    }
    doc.update(extra)
    return doc


def worlds_manifest(generated_at, **extra):
    doc = {"generatedAt": generated_at, "atlasGeneratedAt": "2026-08-30T06:00:00.850Z",
           "objectCount": 35319, "detailRecordCount": 119957}
    doc.update(extra)
    return doc


def deploy(conclusion="success", status="completed", created="2026-09-26T00:16:57Z", history=()):
    run = {"conclusion": conclusion, "status": status, "createdAt": created,
           "headSha": "0c9f1ec94c874d69235e368bfd2bc37ba52bb94c", "displayTitle": "WorldsIndex data: 35319 objects",
           "url": "https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/36204259009"}
    older = [{"conclusion": c, "status": "completed", "createdAt": t, "headSha": "a" * 40, "displayTitle": "older",
              "url": "https://example.test/run"} for c, t in history]
    return [run, *older]


class EvaluateTests(unittest.TestCase):
    def codes(self, report):
        return sorted(row["code"] for row in report["alerts"])

    def test_both_current(self):
        now = at("2026-09-26T00:30:00Z")
        report = watchdog.evaluate(ctas_status("2026-09-26T00:20:00Z"), None,
                                   worlds_manifest("2026-09-26T00:16:46.969Z"), None, deploy(), now=now)
        self.assertTrue(report["ok"], report["alerts"])
        self.assertEqual(report["fingerprint"], "ok")
        self.assertEqual(report["ctas"]["age_hours"], 0.17)
        self.assertFalse(report["ctas"]["certificate_expired"])
        self.assertEqual(report["deploy"]["conclusion"], "success")

    def test_recorded_2026_09_24_outage_is_caught_by_the_cross_check_within_hours(self):
        # 2026-09-24: CTAS last published 05:36Z; WorldsIndex kept publishing (e.g. 21:21Z).
        now = at("2026-09-24T22:00:00Z")
        report = watchdog.evaluate(ctas_status("2026-09-24T05:28:18Z"), None,
                                   worlds_manifest("2026-09-24T21:21:00Z"), None, deploy(), now=now)
        self.assertEqual(self.codes(report), ["ctas-stalled-while-worldsindex-publishes"])
        self.assertTrue(report["ctas"]["certificate_expired"])
        # By the next morning the absolute limit has been crossed as well.
        later = watchdog.evaluate(ctas_status("2026-09-24T05:28:18Z"), None,
                                  worlds_manifest("2026-09-25T01:06:00Z"), None, deploy(),
                                  now=at("2026-09-25T02:00:00Z"))
        self.assertEqual(self.codes(later), ["ctas-stale", "ctas-stalled-while-worldsindex-publishes"])

    def test_cross_check_fires_before_the_absolute_limit(self):
        # CTAS quiet for 4 h while WorldsIndex published 1 h ago: the Mac is up, CTAS is stuck.
        now = at("2026-09-26T04:00:00Z")
        report = watchdog.evaluate(ctas_status("2026-09-26T00:00:00Z"), None,
                                   worlds_manifest("2026-09-26T03:00:00Z"), None, deploy(), now=now)
        self.assertEqual(self.codes(report), ["ctas-stalled-while-worldsindex-publishes"])

    def test_reverse_cross_check_for_worldsindex(self):
        now = at("2026-09-26T04:00:00Z")
        report = watchdog.evaluate(ctas_status("2026-09-26T03:45:00Z"), None,
                                   worlds_manifest("2026-09-25T23:30:00Z"), None, deploy(), now=now)
        self.assertEqual(self.codes(report), ["worldsindex-stalled-while-ctas-publishes"])

    def test_sleeping_mac_is_not_an_alert_until_the_absolute_limit(self):
        # Both quiet for 8 h: a normal idle stretch, below both limits and no cross-check evidence.
        now = at("2026-09-26T14:00:00Z")
        report = watchdog.evaluate(ctas_status("2026-09-26T06:00:00Z"), None,
                                   worlds_manifest("2026-09-26T05:50:00Z"), None, deploy(), now=now)
        self.assertTrue(report["ok"], report["alerts"])
        self.assertTrue(any("normal state while the Mac sleeps" in note for note in report["notes"]))
        # 14 h closed (the laptop is open ~10 h a day): still no alert.
        report = watchdog.evaluate(ctas_status("2026-09-26T06:00:00Z"), None,
                                   worlds_manifest("2026-09-26T05:50:00Z"), None, deploy(),
                                   now=at("2026-09-26T20:00:00Z"))
        self.assertTrue(report["ok"], report["alerts"])
        # 19 h: CTAS crosses its 18 h limit; WorldsIndex (30 h) does not.
        report = watchdog.evaluate(ctas_status("2026-09-26T06:00:00Z"), None,
                                   worlds_manifest("2026-09-26T05:50:00Z"), None, deploy(),
                                   now=at("2026-09-27T01:00:00Z"))
        self.assertEqual(self.codes(report), ["ctas-stale"])

    def test_thresholds_are_configurable(self):
        now = at("2026-09-26T09:00:00Z")
        report = watchdog.evaluate(ctas_status("2026-09-26T04:00:00Z"), None,
                                   worlds_manifest("2026-09-26T04:00:00Z"), None, deploy(), now=now,
                                   ctas_max_age_hours=4, worldsindex_max_age_hours="4.5")
        self.assertEqual(self.codes(report), ["ctas-stale", "worldsindex-stale"])
        self.assertEqual(report["thresholds"]["worldsindex_max_age_hours"], 4.5)

    def test_unreadable_documents_alert(self):
        now = at("2026-09-26T00:30:00Z")
        report = watchdog.evaluate(None, "HTTPError: HTTP Error 404: Not Found",
                                   {"objectCount": 1}, None, deploy(), now=now)
        self.assertEqual(self.codes(report), ["ctas-unavailable", "worldsindex-unavailable"])
        report = watchdog.evaluate(["not", "an", "object"], None,
                                   worlds_manifest("2026-09-26T00:16:46Z"), None, None, now=now)
        self.assertEqual(self.codes(report), ["ctas-unavailable"])
        self.assertFalse(report["deploy"]["known"])

    def test_failed_deploy_alerts_only_when_nothing_deployed_recently(self):
        now = at("2026-09-26T06:30:00Z")
        fresh = (ctas_status("2026-09-26T06:20:00Z"), None, worlds_manifest("2026-09-26T06:16:46Z"), None)
        # failing for 5 h with the last success 5 h ago: frozen site
        stuck = deploy("failure", created="2026-09-26T06:00:00Z",
                       history=[("failure", "2026-09-26T03:00:00Z"), ("success", "2026-09-26T01:30:00Z")])
        report = watchdog.evaluate(*fresh, stuck, now=now)
        self.assertEqual(self.codes(report), ["deploy-failed"])
        self.assertIn("nothing has deployed for 5 h", report["alerts"][0]["message"])
        # no success anywhere in the listed runs
        self.assertEqual(self.codes(watchdog.evaluate(*fresh, deploy("failure"), now=now)), ["deploy-failed"])
        # red right after a CTAS code change, last success 40 min ago: a note, not an alert
        window = deploy("failure", created="2026-09-26T06:10:00Z", history=[("success", "2026-09-26T05:50:00Z")])
        report = watchdog.evaluate(*fresh, window, now=now)
        self.assertTrue(report["ok"], report["alerts"])
        self.assertTrue(any("clears on the next CTAS release" in note for note in report["notes"]))
        # an in-progress newest run is skipped in favour of the newest completed one
        running = [{"status": "in_progress", "conclusion": None, "createdAt": "2026-09-26T06:25:00Z"},
                   *deploy("success", created="2026-09-26T06:05:00Z")]
        self.assertTrue(watchdog.evaluate(*fresh, running, now=now)["ok"])
        self.assertTrue(watchdog.evaluate(*fresh, [], now=now)["ok"])

    def test_timestamp_parsing(self):
        self.assertEqual(watchdog.iso(at("2026-09-26T00:16:46.969Z")), "2026-09-26T00:16:46Z")
        self.assertEqual(watchdog.iso(at("2026-09-25T18:16:53-06:00")), "2026-09-26T00:16:53Z")
        self.assertIsNone(at("yesterday"))
        self.assertIsNone(at(None))
        self.assertIsNone(at(12345))


class RenderAndCliTests(unittest.TestCase):
    def test_markdown_lists_alerts_table_and_mention(self):
        now = at("2026-09-24T22:00:00Z")
        report = watchdog.evaluate(ctas_status("2026-09-24T05:28:18Z"), None,
                                   worlds_manifest("2026-09-24T21:21:00Z"), None, deploy("failure"), now=now)
        text = watchdog.render_markdown(report, "https://example.test", "@owner")
        self.assertIn("**Publisher freshness alert** as of 2026-09-24T22:00:00Z — @owner", text)
        self.assertIn("`ctas-stalled-while-worldsindex-publishes`", text)
        self.assertIn("`deploy-failed`", text)
        self.assertIn("| [CTAS](https://example.test/ctas.html) | 2026-09-24T05:28:18Z | 16.53 h | 18 h |", text)
        self.assertIn("[failure](https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/36204259009)", text)
        self.assertIn("Where to look on the Mac", text)
        ok = watchdog.render_markdown(watchdog.evaluate(
            ctas_status("2026-09-24T21:50:00Z"), None, worlds_manifest("2026-09-24T21:21:00Z"), None, deploy(), now=now),
            "https://example.test", "@owner")
        self.assertIn("**Both publishers are current**", ok)
        self.assertNotIn("@owner", ok)
        self.assertNotIn("Where to look", ok)

    def test_cli_reads_files_writes_report_and_exits_zero_on_alerts(self):
        with tempfile.TemporaryDirectory(prefix="ctas-watchdog-") as folder:
            folder = Path(folder)
            (folder / "status.json").write_text(json.dumps(ctas_status("2026-09-24T05:28:18Z")))
            (folder / "manifest.json").write_text(json.dumps(worlds_manifest("2026-09-24T21:21:00Z")))
            (folder / "deploy.json").write_text(json.dumps(deploy()))
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/watchdog_freshness.py"),
                "--ctas-file", str(folder / "status.json"), "--worldsindex-file", str(folder / "manifest.json"),
                "--deploy-json", str(folder / "deploy.json"), "--now", "2026-09-24T22:00:00Z",
                "--mention", "@owner", "--report", str(folder / "report.json"),
                "--markdown", str(folder / "report.md"), "--ctas-max-age-hours", "18",
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((folder / "report.json").read_text())
            self.assertFalse(report["ok"])
            self.assertEqual(report["fingerprint"], "ctas-stalled-while-worldsindex-publishes")
            self.assertEqual((folder / "report.md").read_text(), result.stdout)

    def test_workflow_wires_the_script_and_the_issue_helper(self):
        workflow = (ROOT / ".github/workflows/freshness-watchdog.yml").read_text()
        self.assertIn("schedule:", workflow)
        self.assertIn("scripts/watchdog_freshness.py", workflow)
        self.assertIn("scripts/watchdog_issue.sh", workflow)
        self.assertIn("issues: write", workflow)
        helper = (ROOT / "scripts/watchdog_issue.sh").read_text()
        for needed in ("gh issue create", "gh issue comment", "gh issue close", "watchdog-fingerprint"):
            self.assertIn(needed, helper)
        self.assertEqual(subprocess.run(["/bin/bash", "-n", str(ROOT / "scripts/watchdog_issue.sh")]).returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
