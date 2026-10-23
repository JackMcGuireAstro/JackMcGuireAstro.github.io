#!/usr/bin/env python3
"""Exercise the publisher freshness watchdog against recorded and synthetic states."""
import datetime as dt
import json
import os
from pathlib import Path
import shutil
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


def runs(*conclusions, start="2026-10-16T12:00:00Z", step_minutes=60, first_id=1000):
    """`gh run list` rows, newest first: conclusions[0] is the newest run."""
    first = at(start)
    rows = []
    for index, conclusion in enumerate(conclusions):
        status = "in_progress" if conclusion == "running" else "completed"
        rows.append({"conclusion": None if conclusion == "running" else conclusion, "status": status,
                     "createdAt": watchdog.iso(first - dt.timedelta(minutes=step_minutes * index)),
                     "databaseId": first_id + index,
                     "url": f"https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/{first_id + index}"})
    return rows


def marker(workflow, generated_at, state_saved_at, run_id="1000", **extra):
    prefix = "soc-" if workflow.startswith("ctas") else "wi-state-"
    suffix = ".db.gz" if workflow.startswith("ctas") else ".tar.gz"
    doc = {"schema": "cloud-run-marker@1", "generated_at": generated_at, "workflow": workflow, "run_id": run_id,
           "run_url": f"https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/{run_id}",
           "state_asset": prefix + state_saved_at.replace("-", "").replace(":", "") + suffix,
           "state_saved_at": state_saved_at, "state_restored_at": generated_at}
    doc.update(extra)
    return doc


def jobs(failed_step="Build, publish and save"):
    return {"jobs": [{"name": "Build and publish", "conclusion": "failure", "steps": [
        {"name": "Site code (main)", "conclusion": "success", "number": 1},
        {"name": failed_step, "conclusion": "failure", "number": 9},
        {"name": "Clean up credentials and scrub the log", "conclusion": "success", "number": 10},
    ]}]}


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
        self.assertTrue(any("the next cloud run renews it" in note for note in report["notes"]))
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
        self.assertTrue(any("but a deployment succeeded" in note for note in report["notes"]))
        # an in-progress newest run is skipped in favour of the newest completed one
        running = [{"status": "in_progress", "conclusion": None, "createdAt": "2026-09-26T06:25:00Z"},
                   *deploy("success", created="2026-09-26T06:05:00Z")]
        self.assertTrue(watchdog.evaluate(*fresh, running, now=now)["ok"])
        self.assertTrue(watchdog.evaluate(*fresh, [], now=now)["ok"])

    def test_consecutive_failed_runs_alert_even_while_the_data_is_fresh(self):
        # 2026-10-14: every WorldsIndex run published, then failed saving its state.
        now = at("2026-10-16T13:00:00Z")
        fresh = (ctas_status("2026-10-16T12:40:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"), None)
        failing = runs("failure", "failure", "failure", "failure", "success", start="2026-10-16T12:20:00Z")
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_runs=runs("success", "success", "failure"),
                                   worldsindex_runs=failing, run_jobs={"1000": jobs()})
        self.assertEqual(self.codes(report), ["worldsindex-runs-failing-after-publish"])
        message = report["alerts"][0]["message"]
        self.assertIn("The last 4 completed runs of worldsindex-cloud.yml failed", message)
        self.assertIn("in step 'Build, publish and save'", message)
        self.assertIn("actions/runs/1000", message)
        self.assertIn("state is not being saved", message)
        summary = report["runs"]["worldsindex"]
        self.assertEqual(summary["consecutive_failures"], 4)
        self.assertEqual(summary["failed_step"], "Build, publish and save")
        self.assertEqual(summary["last_success_at"], "2026-10-16T08:20:00Z")
        self.assertTrue(report["runs"]["ctas"]["known"])
        # Two failures in a row are not yet a streak; the threshold is configurable.
        ok = watchdog.evaluate(*fresh, deploy(), now=now, worldsindex_runs=runs("failure", "failure", "success"))
        self.assertTrue(ok["ok"], ok["alerts"])
        two = watchdog.evaluate(*fresh, deploy(), now=now, worldsindex_runs=runs("failure", "failure", "success"),
                                consecutive_failures=2)
        self.assertEqual(self.codes(two), ["worldsindex-runs-failing-after-publish"])

    def test_failed_runs_without_a_release_since_are_not_publishing(self):
        now = at("2026-10-16T13:00:00Z")
        failing = runs("failure", "failure", "failure", "success", start="2026-10-16T12:20:00Z")
        # CTAS last published before the streak began (10:20Z) but is inside its age limit.
        report = watchdog.evaluate(ctas_status("2026-10-16T10:00:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"),
                                   None, deploy(), now=now, ctas_runs=failing, ctas_max_age_hours=4,
                                   cross_check_stale_hours=4)
        self.assertEqual(self.codes(report), ["ctas-runs-failing"])
        self.assertIn("not publishing at all", report["alerts"][0]["message"])
        self.assertNotIn("in step", report["alerts"][0]["message"])

    def test_failure_rate_over_the_window_alerts_without_a_streak(self):
        now = at("2026-10-16T13:00:00Z")
        fresh = (ctas_status("2026-10-16T12:40:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"), None)
        flapping = runs("success", "failure", "failure", "success", "failure", "failure", "success", "failure",
                        start="2026-10-16T12:30:00Z", step_minutes=45)
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_runs=flapping)
        self.assertEqual(self.codes(report), ["ctas-runs-failing-after-publish"])
        self.assertIn("5 of the last 8 completed runs of ctas-cloud.yml (62.5%) failed in 24 h",
                      report["alerts"][0]["message"])
        # Older than the window, the same failures no longer count.
        old = runs("success", "failure", "failure", "failure", "failure", start="2026-10-16T12:30:00Z",
                   step_minutes=12 * 60)
        self.assertTrue(watchdog.evaluate(*fresh, deploy(), now=now, ctas_runs=old)["ok"])
        # Fewer than four completed runs in the window never trip the rate rule.
        few = runs("success", "failure", "failure", start="2026-10-16T12:30:00Z")
        self.assertTrue(watchdog.evaluate(*fresh, deploy(), now=now, ctas_runs=few)["ok"])

    def test_cancelled_and_running_runs_do_not_count(self):
        now = at("2026-10-16T13:00:00Z")
        fresh = (ctas_status("2026-10-16T12:40:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"), None)
        mixed = runs("running", "cancelled", "failure", "cancelled", "failure", "success", start="2026-10-16T12:50:00Z")
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_runs=mixed)
        self.assertTrue(report["ok"], report["alerts"])
        self.assertEqual(report["runs"]["ctas"]["consecutive_failures"], 2)
        self.assertEqual(report["runs"]["ctas"]["window_completed"], 3)
        self.assertFalse(watchdog.evaluate(*fresh, deploy(), now=now, ctas_runs=[], worldsindex_runs=None)
                         ["runs"]["ctas"]["known"])
        self.assertIsNone(watchdog.failed_step({"jobs": [{"conclusion": "success", "steps": []}]}))
        self.assertIsNone(watchdog.failed_step("not a document"))

    def test_fresh_marker_with_an_old_state_copy_means_the_saves_fail(self):
        # WorldsIndex publishes hourly but every run restores the archive saved on the 14th.
        now = at("2026-10-16T13:00:00Z")
        fresh = (ctas_status("2026-10-16T12:40:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"), None)
        stale_state = marker("worldsindex-cloud.yml", "2026-10-16T12:05:00Z", "2026-10-14T06:10:00Z")
        good_state = marker("ctas-cloud.yml", "2026-10-16T12:20:00Z", "2026-10-16T11:40:00Z")
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=good_state, worldsindex_marker=stale_state)
        self.assertEqual(self.codes(report), ["worldsindex-state-not-saved"])
        message = report["alerts"][0]["message"]
        self.assertIn("restored a working state saved 54.83 h ago (wi-state-20261014T061000Z.tar.gz; limit 6 h)", message)
        self.assertIn("saving the state at the end of each run fails", message)
        self.assertEqual(report["markers"]["ctas"]["state_age_hours"], 1.33)
        self.assertEqual(report["markers"]["worldsindex"]["run_id"], "1000")
        # CTAS: a copy 2.5 h old is normal (runs every ~45 min), 3.5 h is not.
        two_and_a_half = marker("ctas-cloud.yml", "2026-10-16T12:40:00Z", "2026-10-16T10:30:00Z")
        self.assertTrue(watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=two_and_a_half)["ok"])
        late = marker("ctas-cloud.yml", "2026-10-16T12:40:00Z", "2026-10-16T09:20:00Z")
        self.assertEqual(self.codes(watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=late)),
                         ["ctas-state-not-saved"])
        # The limits are configurable.
        self.assertTrue(watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=late, ctas_state_max_age_hours=5)["ok"])

    def test_stale_marker_leaves_the_state_check_to_the_freshness_alerts(self):
        # CTAS stopped publishing 5 h ago: its marker is as old as its release, and the
        # restored copy ages with it. That is the stall, not a save failure.
        now = at("2026-10-16T13:00:00Z")
        old = marker("ctas-cloud.yml", "2026-10-16T08:00:00Z", "2026-10-16T07:20:00Z")
        report = watchdog.evaluate(ctas_status("2026-10-16T08:00:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"),
                                   None, deploy(), now=now, ctas_marker=old, ctas_max_age_hours=4)
        self.assertEqual(self.codes(report), ["ctas-stale", "ctas-stalled-while-worldsindex-publishes"])

    def test_missing_or_unreadable_marker_is_a_note_not_an_alert(self):
        now = at("2026-10-16T13:00:00Z")
        fresh = (ctas_status("2026-10-16T12:40:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"), None)
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker_error="HTTPError: HTTP Error 404: Not Found",
                                   worldsindex_marker=["not", "a", "marker"])
        self.assertTrue(report["ok"], report["alerts"])
        self.assertTrue(any("No cloud-run marker for CTAS yet (HTTPError" in note for note in report["notes"]))
        self.assertTrue(any("No cloud-run marker for WorldsIndex yet" in note for note in report["notes"]))
        unreadable = marker("ctas-cloud.yml", "2026-10-16T12:40:00Z", "garbage")
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=unreadable)
        self.assertTrue(report["ok"], report["alerts"])
        self.assertTrue(any("no readable state_saved_at" in note for note in report["notes"]))

    def test_state_key_expiry_and_rejection(self):
        now = at("2026-10-16T13:00:00Z")
        fresh = (ctas_status("2026-10-16T12:40:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"), None)
        # 25 days left: quiet. 10 days: alert. Expired: alert with the days since.
        far = marker("ctas-cloud.yml", "2026-10-16T12:40:00Z", "2026-10-16T12:00:00Z", token_status="ok",
                     token_expires_at="2026-11-10", token_checked_at="2026-10-16T12:30:00Z")
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=far)
        self.assertTrue(report["ok"], report["alerts"])
        self.assertEqual(report["token"]["token_days_left"], 25)
        soon = dict(far, token_expires_at="2026-10-26")
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=soon)
        self.assertEqual(self.codes(report), ["state-token-expiring"])
        self.assertIn("expires on 2026-10-26 (10 days left)", report["alerts"][0]["message"])
        gone = dict(far, token_expires_at="2026-10-15")
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=gone)
        self.assertIn("expired on 2026-10-15 (1 day ago)", report["alerts"][0]["message"])
        self.assertTrue(watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=soon, token_warn_days=5)["ok"])
        # A key without an expiry date (gh's OAuth sign-in) never warns.
        self.assertTrue(watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=dict(far, token_expires_at=None))["ok"])
        # Rejected: the most recently checked marker wins, whichever workflow published it.
        rejected = marker("worldsindex-cloud.yml", "2026-10-16T12:50:00Z", "2026-10-16T11:50:00Z",
                          token_status="unauthorized", token_expires_at=None, token_checked_at="2026-10-16T12:45:00Z")
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=far, worldsindex_marker=rejected)
        self.assertEqual(self.codes(report), ["state-token-rejected"])
        self.assertIn("HTTP 401", report["alerts"][0]["message"])
        older_rejection = dict(rejected, token_checked_at="2026-10-16T11:45:00Z")
        self.assertTrue(watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=far,
                                          worldsindex_marker=older_rejection)["ok"])
        # Markers from before the probe existed, or an unreachable API, say nothing.
        unknown = dict(far, token_status="unknown", token_expires_at=None)
        report = watchdog.evaluate(*fresh, deploy(), now=now, ctas_marker=unknown)
        self.assertTrue(report["ok"])
        self.assertEqual(report["token"]["token_status"], "not-checked")

    def test_timestamp_parsing(self):
        self.assertEqual(watchdog.iso(at("2026-09-26T00:16:46.969Z")), "2026-09-26T00:16:46Z")
        self.assertEqual(watchdog.iso(at("2026-09-25T18:16:53-06:00")), "2026-09-26T00:16:53Z")
        self.assertIsNone(at("yesterday"))
        self.assertIsNone(at(None))
        self.assertIsNone(at(12345))


class RenderAndCliTests(unittest.TestCase):
    def test_first_line_of_an_alert_says_what_to_do(self):
        now = at("2026-10-16T13:00:00Z")
        fresh = (ctas_status("2026-10-16T12:40:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"), None)
        failing = runs("failure", "failure", "failure", "success", start="2026-10-16T12:20:00Z")
        stale_state = marker("worldsindex-cloud.yml", "2026-10-16T12:05:00Z", "2026-10-14T06:10:00Z",
                             token_status="ok", token_expires_at="2026-10-20", token_checked_at="2026-10-16T12:05:00Z")
        report = watchdog.evaluate(*fresh, deploy(), now=now, worldsindex_runs=failing, run_jobs={"1000": jobs()},
                                   worldsindex_marker=stale_state)
        # most urgent first: the failing runs, then the stale state, then the key
        self.assertEqual([row["code"] for row in report["alerts"]],
                         ["worldsindex-runs-failing-after-publish", "worldsindex-state-not-saved", "state-token-expiring"])
        text = watchdog.render_markdown(report, "https://example.test", "@owner")
        first = text.splitlines()[0]
        self.assertTrue(first.startswith("**Do this first:** Open the newest failed run of worldsindex-cloud.yml "
                                         "(https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/1000)"), first)
        self.assertIn("failed in step 'Build, publish and save', most likely saving the state", first)
        self.assertIn("`worldsindex-publish-log` artifact", first)
        self.assertIn("  - To do: Before 2026-10-20, run `Renew cloud key.command`", text)
        self.assertIn("| State key (CTAS_STATE_TOKEN) | 2026-10-16T12:05:00Z | — | 10 days | ok; expires 2026-10-20 (4 days) |", text)
        self.assertIn('CTAS-AUTOMATION.md, "When an alert fires"', text)
        self.assertTrue(report["summary"].startswith("Do this first: Open the newest failed run"))
        self.assertIn("- state-token-expiring: The CTAS_STATE_TOKEN key expires on 2026-10-20", report["summary"])
        # every alert code has an action of its own, and a rejected key outranks everything
        rejected = dict(stale_state, token_status="unauthorized", token_expires_at=None)
        report = watchdog.evaluate(None, "HTTPError: 404", None, "HTTPError: 404", deploy("failure"), now=now,
                                   ctas_runs=failing, worldsindex_marker=rejected)
        self.assertEqual(report["alerts"][0]["code"], "state-token-rejected")
        self.assertIn("run `Renew cloud key.command` on the Mac", report["alerts"][0]["action"])
        actions = {row["code"]: row["action"] for row in report["alerts"]}
        self.assertIn("ctas-cloud.yml", actions["ctas-runs-failing"])
        self.assertIn("ctas/data/status.json", actions["ctas-unavailable"])
        self.assertIn("actions/runs/36204259009", actions["deploy-failed"])
        stalled = watchdog.evaluate(ctas_status("2026-10-16T06:00:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"),
                                    None, deploy(), now=now, ctas_max_age_hours=4, ctas_marker=marker(
                                        "ctas-cloud.yml", "2026-10-16T06:00:00Z", "2026-10-16T05:20:00Z"))
        self.assertEqual([row["code"] for row in stalled["alerts"]], ["ctas-stalled-while-worldsindex-publishes", "ctas-stale"])
        self.assertIn('Actions -> "CTAS cloud collector and publisher"', stalled["alerts"][0]["action"])
        not_saved = watchdog.evaluate(*fresh, deploy(), now=now, worldsindex_marker=stale_state, token_warn_days=1)
        self.assertEqual([row["code"] for row in not_saved["alerts"]], ["worldsindex-state-not-saved"])
        self.assertIn("restoring a state saved 2026-10-14T06:10:00Z", not_saved["alerts"][0]["action"])
        self.assertEqual(watchdog.evaluate(*fresh, deploy(), now=now)["summary"],
                         "Both publishers are current as of 2026-10-16T13:00:00Z.")

    def test_markdown_lists_alerts_table_and_mention(self):
        now = at("2026-09-24T22:00:00Z")
        report = watchdog.evaluate(ctas_status("2026-09-24T05:28:18Z"), None,
                                   worlds_manifest("2026-09-24T21:21:00Z"), None, deploy("failure"), now=now)
        text = watchdog.render_markdown(report, "https://example.test", "@owner")
        self.assertIn("**Publisher freshness alert** as of 2026-09-24T22:00:00Z — @owner", text)
        self.assertTrue(text.startswith("**Do this first:** Open the newest run of ctas-cloud.yml"), text[:120])
        self.assertIn("`ctas-stalled-while-worldsindex-publishes`", text)
        self.assertIn("`deploy-failed`", text)
        self.assertIn("| [CTAS](https://example.test/ctas.html) | 2026-09-24T05:28:18Z | 16.53 h | 18 h |", text)
        self.assertIn("[failure](https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/36204259009)", text)
        self.assertIn("Where to look", text)
        self.assertIn("Renew cloud key.command", text)
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
            (folder / "wi-runs.json").write_text(json.dumps(runs("failure", "failure", "failure", "success",
                                                                 start="2026-09-24T21:30:00Z")))
            (folder / "run-jobs").mkdir()
            (folder / "run-jobs" / "1000.json").write_text(json.dumps(jobs("Build, publish and save")))
            (folder / "run-jobs" / "stray.txt").write_text("ignored")
            (folder / "ctas-marker.json").write_text(json.dumps(
                marker("ctas-cloud.yml", "2026-09-24T05:20:00Z", "2026-09-24T04:40:00Z")))
            (folder / "wi-marker.json").write_text(json.dumps(
                marker("worldsindex-cloud.yml", "2026-09-24T21:15:00Z", "2026-09-24T20:10:00Z", run_id="1001",
                       token_status="ok", token_expires_at="2026-11-15", token_checked_at="2026-09-24T21:15:00Z")))
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/watchdog_freshness.py"),
                "--ctas-file", str(folder / "status.json"), "--worldsindex-file", str(folder / "manifest.json"),
                "--deploy-json", str(folder / "deploy.json"), "--now", "2026-09-24T22:00:00Z",
                "--ctas-runs-json", str(folder / "missing.json"), "--worldsindex-runs-json", str(folder / "wi-runs.json"),
                "--run-jobs-dir", str(folder / "run-jobs"),
                "--ctas-marker-file", str(folder / "ctas-marker.json"),
                "--worldsindex-marker-file", str(folder / "wi-marker.json"),
                "--mention", "@owner", "--report", str(folder / "report.json"),
                "--markdown", str(folder / "report.md"), "--ctas-max-age-hours", "18",
                "--raw-base", "",   # no data-branch reads in this test (no network)
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CTAS run list unavailable", result.stderr)
            report = json.loads((folder / "report.json").read_text())
            self.assertFalse(report["ok"])
            self.assertEqual(report["fingerprint"],
                             "ctas-stalled-while-worldsindex-publishes|worldsindex-runs-failing-after-publish")
            self.assertEqual(report["runs"]["worldsindex"]["failed_step"], "Build, publish and save")
            self.assertFalse(report["runs"]["ctas"]["known"])
            self.assertEqual((folder / "report.md").read_text(), result.stdout)
            self.assertEqual(report["markers"]["worldsindex"]["state_age_hours"], 1.83)
            self.assertIn("| State key (CTAS_STATE_TOKEN) | 2026-09-24T21:15:00Z | — | 10 days | ok; expires 2026-11-15 (52 days) |",
                          result.stdout)
            self.assertIn("| WorldsIndex state save | 2026-09-24T20:10:00Z | 1.83 h | 6 h | wi-state-20260924T201000Z.tar.gz, "
                          "restored by [run 1001](https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/1001) "
                          "0.75 h ago |", result.stdout)
            self.assertIn("| Runs of worldsindex-cloud.yml | 2026-09-24T21:30:00Z | — | 3 in a row | "
                          "[failure](https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/1000); "
                          "3 failed in a row; 3/4 failed in 24 h; failed step: Build, publish and save |", result.stdout)

    def test_workflow_wires_the_script_and_the_issue_helper(self):
        workflow = (ROOT / ".github/workflows/freshness-watchdog.yml").read_text()
        self.assertIn("schedule:", workflow)
        self.assertIn("scripts/watchdog_freshness.py", workflow)
        self.assertIn("scripts/watchdog_issue.sh", workflow)
        self.assertIn("issues: write", workflow)
        self.assertIn("actions: read", workflow)
        for needed in ('--workflow "$workflow.yml"', "--json conclusion,status,createdAt,url,databaseId",
                       "gh run view", "--json jobs", "--ctas-runs-json ctas-cloud-runs.json",
                       "--worldsindex-runs-json worldsindex-cloud-runs.json", "--run-jobs-dir run-jobs",
                       '--consecutive-failures "$CONSECUTIVE_FAILURES"',
                       '--ctas-state-max-age-hours "$CTAS_STATE_MAX_AGE_HOURS"', '--token-warn-days "$TOKEN_WARN_DAYS"'):
            self.assertIn(needed, workflow)
        self.assertIn("WATCHDOG_NOTIFY_WEBHOOK: ${{ secrets.WATCHDOG_NOTIFY_WEBHOOK }}", workflow)
        helper = (ROOT / "scripts/watchdog_issue.sh").read_text()
        for needed in ("gh issue create", "gh issue comment", "gh issue close", "watchdog-fingerprint",
                       "WATCHDOG_NOTIFY_WEBHOOK", 'notify "opened"', 'notify "changed"', 'notify "recovered"'):
            self.assertIn(needed, helper)
        self.assertEqual(subprocess.run(["/bin/bash", "-n", str(ROOT / "scripts/watchdog_issue.sh")]).returncode, 0)


class LiveSiteTests(unittest.TestCase):
    """Item 7: the public site against the data branches (2026-10-21: the deploy stopped for
    41 hours while ctas-data and worldsindex-data kept updating, and nothing alerted)."""

    NOW = "2026-10-21T18:00:00Z"

    def deploy_runs(self):
        return [{"conclusion": "failure", "status": "completed", "createdAt": "2026-10-21T17:40:00Z", "headSha": "e" * 40,
                 "displayTitle": "Validate and deploy site", "databaseId": 555,
                 "url": "https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/555"},
                {"conclusion": "success", "status": "completed", "createdAt": "2026-10-20T01:10:00Z", "headSha": "d" * 40,
                 "displayTitle": "Validate and deploy site", "databaseId": 400,
                 "url": "https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/400"}]

    def failed_jobs(self):
        return {"jobs": [{"name": "Validate all website releases", "conclusion": "failure", "steps": [
            {"name": "Set up job", "conclusion": "success"},
            {"name": "CTAS page in a real browser, and evidence schema", "conclusion": "failure"}]}]}

    def test_a_frozen_site_with_fresh_branches_is_an_alert_naming_the_deploy_and_the_step(self):
        report = watchdog.evaluate(ctas_status("2026-10-20T01:00:00Z"), None, worlds_manifest("2026-10-20T00:50:00Z"), None,
                                   self.deploy_runs(), now=at(self.NOW),
                                   ctas_branch=ctas_status("2026-10-21T17:45:00Z"),
                                   worldsindex_branch=worlds_manifest("2026-10-21T17:41:00Z"),
                                   deploy_jobs=self.failed_jobs(), ctas_max_age_hours=4, worldsindex_max_age_hours=6)
        codes = [row["code"] for row in report["alerts"]]
        self.assertEqual(codes[0], "deploy-stale")
        message = report["alerts"][0]["message"]
        self.assertIn("Public site is not deploying", message)
        self.assertIn("CTAS live 2026-10-20T01:00:00Z vs ctas-data 2026-10-21T17:45:00Z (2445 min behind)", message)
        self.assertIn("WorldsIndex live 2026-10-20T00:50:00Z vs worldsindex-data 2026-10-21T17:41:00Z", message)
        self.assertIn("actions/runs/400", message)
        self.assertIn("step 'CTAS page in a real browser, and evidence schema'", message)
        self.assertIn("actions/runs/555", report["alerts"][0]["action"])
        self.assertEqual(report["site"]["ctas"]["lag_minutes"], 2445.0)
        markdown = watchdog.render_markdown(report, "https://example.test")
        self.assertIn("| CTAS live vs ctas-data | 2026-10-21T17:45:00Z | 2445 min behind | 60 min |", markdown)

    def test_the_lag_limit_is_sixty_minutes_and_an_unknown_branch_is_no_alert(self):
        def codes(live, branch, **kwargs):
            report = watchdog.evaluate(ctas_status(live), None, worlds_manifest("2026-10-21T17:20:00Z"), None,
                                       self.deploy_runs()[1:], now=at(self.NOW), ctas_branch=ctas_status(branch) if branch else None,
                                       **kwargs)
            return [row["code"] for row in report["alerts"]]
        self.assertNotIn("deploy-stale", codes("2026-10-21T17:30:00Z", "2026-10-21T17:55:00Z"))    # 25 min behind
        self.assertNotIn("deploy-stale", codes("2026-10-21T16:55:00Z", "2026-10-21T17:55:00Z"))    # exactly 60 min
        self.assertIn("deploy-stale", codes("2026-10-21T16:54:00Z", "2026-10-21T17:55:00Z"))       # 61 min
        # A branch that is itself old still counts: the site is behind what was published.
        self.assertIn("deploy-stale", codes("2026-10-20T01:00:00Z", "2026-10-21T08:00:00Z", ctas_max_age_hours=4))
        self.assertIn("deploy-stale", codes("2026-10-21T15:00:00Z", "2026-10-21T17:55:00Z", site_lag_minutes=120))
        self.assertNotIn("deploy-stale", codes("2026-10-21T16:00:00Z", "2026-10-21T17:55:00Z", site_lag_minutes=120))
        self.assertNotIn("deploy-stale", codes("2026-10-21T17:30:00Z", None))
        self.assertFalse(watchdog.evaluate(ctas_status("2026-10-21T17:30:00Z"), None, worlds_manifest("2026-10-21T17:20:00Z"), None,
                                           None, now=at(self.NOW))["site"]["ctas"]["known"])

    def test_branches_are_read_from_raw_githubusercontent_with_a_mocked_fetch(self):
        calls = []

        def fake_fetch(url, attempts=3, timeout=30):
            calls.append(url)
            if url.endswith("/ctas-data/ctas/data/status.json"):
                return ctas_status("2026-10-21T17:45:00Z"), None
            return None, "HTTPError: HTTP Error 404: Not Found"
        args = type("Args", (), {"ctas_branch_file": None, "worldsindex_branch_file": None,
                                 "raw_base": "https://raw.githubusercontent.com/owner/repo/"})()
        ctas_branch, worlds_branch = watchdog.read_branches(args, lambda path, label: None, fetch=fake_fetch)
        self.assertEqual(calls, ["https://raw.githubusercontent.com/owner/repo/ctas-data/ctas/data/status.json",
                                 "https://raw.githubusercontent.com/owner/repo/worldsindex-data/worldsindex/data/manifest.json"])
        self.assertEqual(ctas_branch["last_successful_update"], "2026-10-21T17:45:00Z")
        self.assertIsNone(worlds_branch)
        self.assertEqual(watchdog.RAW_BASE, "https://raw.githubusercontent.com/JackMcGuireAstro/JackMcGuireAstro.github.io")
        args.raw_base = ""
        self.assertEqual(watchdog.read_branches(args, lambda path, label: None, fetch=fake_fetch), (None, None))

    def test_main_reads_the_live_site_and_the_branches_over_http(self):
        # One local HTTP server stands in for both the live site and raw.githubusercontent.com.
        import http.server
        import threading
        with tempfile.TemporaryDirectory(prefix="watchdog-live-") as folder:
            folder = Path(folder)
            for relative, document in (("site/ctas/data/status.json", ctas_status("2026-10-20T01:00:00Z")),
                                       ("site/worldsindex/data/manifest.json", worlds_manifest("2026-10-21T17:30:00Z")),
                                       ("site/raw/ctas-data/ctas/data/status.json", ctas_status("2026-10-21T17:45:00Z")),
                                       ("site/raw/worldsindex-data/worldsindex/data/manifest.json", worlds_manifest("2026-10-21T17:30:00Z"))):
                (folder / relative).parent.mkdir(parents=True, exist_ok=True)
                (folder / relative).write_text(json.dumps(document))
            (folder / "deploy.json").write_text(json.dumps(self.deploy_runs()))
            (folder / "deploy-jobs.json").write_text(json.dumps(self.failed_jobs()))
            (folder / "marker.json").write_text("{}")
            requests = []

            class Handler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=str(folder / "site"), **kwargs)

                def log_message(self, *args):
                    requests.append(self.path)

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                env = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
                done = subprocess.run([sys.executable, str(ROOT / "scripts/watchdog_freshness.py"), "--base", base,
                                       "--raw-base", f"{base}/raw",
                                       "--deploy-json", str(folder / "deploy.json"), "--deploy-jobs-json", str(folder / "deploy-jobs.json"),
                                       "--ctas-marker-file", str(folder / "marker.json"),
                                       "--worldsindex-marker-file", str(folder / "marker.json"),
                                       "--now", self.NOW, "--ctas-max-age-hours", "40", "--site-lag-minutes", "60",
                                       "--report", str(folder / "report.json")], text=True, capture_output=True, env=env)
            finally:
                server.shutdown()
            self.assertEqual(done.returncode, 0, done.stderr)
            report = json.loads((folder / "report.json").read_text())
            self.assertIn("deploy-stale", [row["code"] for row in report["alerts"]])
            self.assertEqual(report["site"]["ctas"]["lag_minutes"], 2445.0)
            self.assertEqual(report["site"]["worldsindex"]["lag_minutes"], 0.0)
            self.assertTrue(any(path.startswith("/ctas/data/status.json?watchdog=") for path in requests))
            self.assertTrue(any(path.startswith("/raw/ctas-data/ctas/data/status.json?watchdog=") for path in requests))
            self.assertTrue(any(path.startswith("/raw/worldsindex-data/worldsindex/data/manifest.json?watchdog=") for path in requests))

    def test_the_hourly_workflow_reads_the_branches_and_the_failed_deploy_step(self):
        workflow = (ROOT / ".github/workflows/freshness-watchdog.yml").read_text()
        for needed in ('--raw-base "https://raw.githubusercontent.com/$GITHUB_REPOSITORY"',
                       "--json conclusion,status,createdAt,headSha,url,displayTitle,databaseId",
                       'gh run view "$id" --repo "$GITHUB_REPOSITORY" --json jobs > deploy-jobs.json',
                       "--deploy-jobs-json deploy-jobs.json", '--site-lag-minutes "$SITE_LAG_MINUTES"', 'SITE_LAG_MINUTES: "60"',
                       'cron: "23 * * * *"'):
            self.assertIn(needed, workflow)


FAKE_GH = r'''#!/usr/bin/env python3
"""Stand-in for the GitHub CLI: records every call under FAKE_DIR, answers from the environment."""
import os, shutil, sys
from pathlib import Path
out = Path(os.environ["FAKE_DIR"])
args = sys.argv[1:]
with open(out / "gh-calls.log", "a") as log:
    log.write(" ".join(args[:2]) + "\n")
if args[:2] == ["label", "create"]:
    sys.exit(0)
if args[:2] == ["issue", "list"]:
    print(os.environ.get("FAKE_OPEN_ISSUE", ""))
elif args[:2] == ["issue", "create"]:
    (out / "created-title.txt").write_text(args[args.index("--title") + 1])
    shutil.copy(args[args.index("--body-file") + 1], out / "created-body.md")
    print("https://github.com/owner/repo/issues/77")
elif args[:2] == ["issue", "view"]:
    print(os.environ.get("FAKE_LAST", "none 2026-10-16T00:00:00Z"))
elif args[:2] == ["issue", "comment"]:
    shutil.copy(args[args.index("--body-file") + 1], out / "comment-body.md")
elif args[:2] == ["issue", "close"]:
    (out / "closed.md").write_text(args[args.index("--comment") + 1])
else:
    print("unexpected gh call: %r" % args, file=sys.stderr); sys.exit(2)
'''

FAKE_CURL = r'''#!/usr/bin/env python3
"""Stand-in for curl: records the arguments and the posted body under FAKE_DIR."""
import os, sys
from pathlib import Path
out = Path(os.environ["FAKE_DIR"])
args = sys.argv[1:]
body = args[args.index("--data-binary") + 1]
if body == "@-":
    body = sys.stdin.read()
(out / "curl-args.txt").write_text("\n".join(args))
(out / "curl-body.txt").write_text(body)
sys.exit(int(os.environ.get("FAKE_CURL_EXIT", "0")))
'''


class IssueHelperTests(unittest.TestCase):
    """scripts/watchdog_issue.sh end to end, against stand-ins for gh and curl (no network)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ctas-watchdog-issue-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name, text in (("gh", FAKE_GH), ("curl", FAKE_CURL)):
            (bin_dir / name).write_text(text)
            (bin_dir / name).chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_DIR": str(self.tmp),
                    "GITHUB_REPOSITORY": "owner/repo", "WATCHDOG_REMIND_HOURS": "12"}
        self.env.pop("WATCHDOG_NOTIFY_WEBHOOK", None)
        self.env.pop("WATCHDOG_NOTIFY_FORMAT", None)

    def alerting_report(self):
        now = at("2026-10-16T13:00:00Z")
        failing = runs("failure", "failure", "failure", "success", start="2026-10-16T12:20:00Z")
        return watchdog.evaluate(ctas_status("2026-10-16T12:40:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"), None,
                                 deploy(), now=now, worldsindex_runs=failing, run_jobs={"1000": jobs()})

    def quiet_report(self):
        now = at("2026-10-16T13:00:00Z")
        return watchdog.evaluate(ctas_status("2026-10-16T12:40:00Z"), None, worlds_manifest("2026-10-16T12:35:00Z"), None,
                                 deploy(), now=now)

    def run_helper(self, report, **env):
        (self.tmp / "report.json").write_text(json.dumps(report))
        (self.tmp / "report.md").write_text(watchdog.render_markdown(report, "https://example.test", "@owner"))
        result = subprocess.run(["/bin/bash", str(ROOT / "scripts/watchdog_issue.sh"), "report.json", "report.md"],
                                cwd=self.tmp, env={**self.env, **env}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def calls(self):
        return (self.tmp / "gh-calls.log").read_text().split()

    def test_opens_an_issue_whose_first_line_says_what_to_do_and_notifies_slack_style(self):
        out = self.run_helper(self.alerting_report(), WATCHDOG_NOTIFY_WEBHOOK="https://hooks.slack.test/services/T/B/x")
        self.assertIn("opened https://github.com/owner/repo/issues/77", out)
        self.assertIn("notified the webhook (opened)", out)
        self.assertEqual((self.tmp / "created-title.txt").read_text(), "Publisher alert: WorldsIndex")
        body = (self.tmp / "created-body.md").read_text()
        self.assertTrue(body.startswith("**Do this first:** Open the newest failed run of worldsindex-cloud.yml"), body[:100])
        self.assertIn("<!-- watchdog-fingerprint: worldsindex-runs-failing-after-publish -->", body)
        posted = json.loads((self.tmp / "curl-body.txt").read_text())
        self.assertTrue(posted["text"].startswith("Publisher watchdog: opened (WorldsIndex)\nDo this first: Open the newest failed run"))
        self.assertIn("https://github.com/owner/repo/issues/77", posted["text"])
        self.assertEqual(posted["content"], posted["text"][:1900])
        self.assertIn("Content-Type: application/json", (self.tmp / "curl-args.txt").read_text())

    def test_ntfy_gets_plain_text_with_a_title_and_a_failed_post_is_not_fatal(self):
        out = self.run_helper(self.alerting_report(), WATCHDOG_NOTIFY_WEBHOOK="https://ntfy.sh/my-private-topic")
        self.assertIn("notified the webhook (opened)", out)
        args = (self.tmp / "curl-args.txt").read_text().splitlines()
        self.assertIn("Title: Publisher watchdog: opened (WorldsIndex)", args)
        self.assertTrue((self.tmp / "curl-body.txt").read_text().startswith("Do this first: "))
        self.assertNotIn("Content-Type: application/json", args)
        out = self.run_helper(self.alerting_report(), WATCHDOG_NOTIFY_WEBHOOK="https://example.test/hook",
                              WATCHDOG_NOTIFY_FORMAT="text", FAKE_CURL_EXIT="22")
        self.assertIn("webhook notification failed (opened); the issue is the record", out)
        self.assertIn("Title: Publisher watchdog", (self.tmp / "curl-args.txt").read_text())

    def test_a_frozen_public_site_gets_the_plain_title(self):
        now = at("2026-10-21T18:00:00Z")
        report = watchdog.evaluate(ctas_status("2026-10-20T01:00:00Z"), None, worlds_manifest("2026-10-21T17:30:00Z"), None,
                                   deploy("failure", created="2026-10-21T17:00:00Z", history=[("success", "2026-10-20T01:10:00Z")]),
                                   now=now, ctas_branch=ctas_status("2026-10-21T17:45:00Z"), ctas_max_age_hours=40)
        self.run_helper(report)
        self.assertEqual((self.tmp / "created-title.txt").read_text(), "Public site is not deploying (also: CTAS, deployment)")

    def test_quiet_case_touches_nothing_and_recovery_closes_and_notifies(self):
        out = self.run_helper(self.quiet_report(), WATCHDOG_NOTIFY_WEBHOOK="https://ntfy.sh/topic")
        self.assertIn("both publishers current; no open alert issue", out)
        self.assertEqual(self.calls(), ["label", "create", "issue", "list"])
        self.assertFalse((self.tmp / "curl-args.txt").exists())
        out = self.run_helper(self.quiet_report(), WATCHDOG_NOTIFY_WEBHOOK="https://ntfy.sh/topic", FAKE_OPEN_ISSUE="77")
        self.assertIn("closed issue #77 (recovered)", out)
        self.assertTrue((self.tmp / "closed.md").read_text().startswith("Recovered: both publishers are current again."))
        self.assertIn("Title: Publisher watchdog: recovered (ok)", (self.tmp / "curl-args.txt").read_text())
        self.assertIn("https://github.com/owner/repo/issues/77", (self.tmp / "curl-body.txt").read_text())

    def test_open_issue_is_updated_only_when_the_picture_changes_or_a_reminder_is_due(self):
        # The helper measures the last update's age against the real clock.
        recent = watchdog.iso(dt.datetime.now(UTC) - dt.timedelta(hours=1))
        long_ago = watchdog.iso(dt.datetime.now(UTC) - dt.timedelta(hours=13))
        out = self.run_helper(self.alerting_report(), FAKE_OPEN_ISSUE="77",
                              FAKE_LAST=f"worldsindex-runs-failing-after-publish {recent}",
                              WATCHDOG_NOTIFY_WEBHOOK="https://ntfy.sh/topic")
        self.assertIn("issue #77 unchanged", out)
        self.assertFalse((self.tmp / "comment-body.md").exists())
        self.assertFalse((self.tmp / "curl-args.txt").exists())
        out = self.run_helper(self.alerting_report(), FAKE_OPEN_ISSUE="77", FAKE_LAST=f"ctas-stale {recent}",
                              WATCHDOG_NOTIFY_WEBHOOK="https://ntfy.sh/topic")
        self.assertIn("updated issue #77 (ctas-stale -> worldsindex-runs-failing-after-publish", out)
        self.assertTrue((self.tmp / "comment-body.md").read_text().startswith("**Do this first:**"))
        self.assertIn("Title: Publisher watchdog: changed (WorldsIndex)", (self.tmp / "curl-args.txt").read_text())
        (self.tmp / "curl-args.txt").unlink()
        out = self.run_helper(self.alerting_report(), FAKE_OPEN_ISSUE="77",
                              FAKE_LAST=f"worldsindex-runs-failing-after-publish {long_ago}",
                              WATCHDOG_NOTIFY_WEBHOOK="https://ntfy.sh/topic")
        self.assertIn("updated issue #77", out)  # a reminder: a comment, but no new notification
        self.assertFalse((self.tmp / "curl-args.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
