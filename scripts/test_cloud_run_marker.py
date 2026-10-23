#!/usr/bin/env python3
"""Tests for scripts/cloud_run_marker.py and its wiring into the cloud runs (no network)."""
from __future__ import annotations

import email.message
import io
import json
import subprocess
import urllib.error
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cloud_run_marker as marker  # noqa: E402

ACTIONS_ENV = {"GITHUB_RUN_ID": "18527", "GITHUB_SERVER_URL": "https://github.com",
               "GITHUB_REPOSITORY": "JackMcGuireAstro/JackMcGuireAstro.github.io"}


class FakeResponse(io.BytesIO):
    def __init__(self, status, headers):
        super().__init__(b"{}")
        self.status = status
        self.headers = email.message.Message()
        for name, value in headers.items():
            self.headers[name] = value


def opener_returning(status, headers=None, error=None):
    def opener(request, timeout):
        assert request.full_url == marker.TOKEN_PROBE_URL
        assert request.get_header("Authorization") == "Bearer ghp_fixture"
        if error:
            raise error
        if status >= 400:
            response = FakeResponse(status, headers or {})
            raise urllib.error.HTTPError(request.full_url, status, "nope", response.headers, response)
        return FakeResponse(status, headers or {})
    return opener


class MarkerTests(unittest.TestCase):
    def test_token_probe_records_acceptance_and_expiry_date_only(self):
        accepted = marker.probe_token("ghp_fixture", opener_returning(
            200, {marker.TOKEN_EXPIRY_HEADER: "2026-11-15 10:00:00 UTC", "X-OAuth-Scopes": "repo"}))
        self.assertEqual(accepted["token_status"], "ok")
        self.assertEqual(accepted["token_expires_at"], "2026-11-15")
        self.assertEqual(accepted["token_http_status"], 200)
        forever = marker.probe_token("ghp_fixture", opener_returning(200, {}))
        self.assertEqual((forever["token_status"], forever["token_expires_at"]), ("ok", None))
        rejected = marker.probe_token("ghp_fixture", opener_returning(401, {}))
        self.assertEqual((rejected["token_status"], rejected["token_http_status"]), ("unauthorized", 401))
        outage = marker.probe_token("ghp_fixture", opener_returning(503, {}))
        self.assertEqual(outage["token_status"], "unknown")
        offline = marker.probe_token("ghp_fixture", opener_returning(0, error=urllib.error.URLError("no route")))
        self.assertEqual((offline["token_status"], offline["token_probe_error"]), ("unknown", "URLError"))
        self.assertEqual(marker.probe_token("", None)["token_status"], "not-checked")
        for result in (accepted, forever, rejected, outage, offline):
            self.assertNotIn("ghp_fixture", json.dumps(result))
        self.assertEqual(marker.parse_expiry("2026-11-15 10:00:00 +0000"), "2026-11-15")
        self.assertIsNone(marker.parse_expiry(None))
        self.assertIsNone(marker.parse_expiry("soon"))
        doc = marker.build_marker("ctas-cloud.yml", {}, {}, generated_at="2026-10-16T07:02:11Z", token=accepted)
        self.assertEqual(doc["token_expires_at"], "2026-11-15")
        self.assertEqual(marker.build_marker("ctas-cloud.yml", {}, {})["token_status"], "not-checked")

    def test_marker_names_the_restored_state_and_the_run(self):
        receipt = {"asset": "wi-state-20261016T061500Z.tar.gz", "saved_at": "2026-10-16T06:15:00Z",
                   "restored_at": "2026-10-16T07:02:10Z"}
        doc = marker.build_marker("worldsindex-cloud.yml", receipt, ACTIONS_ENV, generated_at="2026-10-16T07:02:11Z")
        self.assertEqual(doc, {
            "schema": "cloud-run-marker@1", "generated_at": "2026-10-16T07:02:11Z",
            "workflow": "worldsindex-cloud.yml", "run_id": "18527",
            "run_url": "https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/actions/runs/18527",
            "state_asset": "wi-state-20261016T061500Z.tar.gz", "state_saved_at": "2026-10-16T06:15:00Z",
            "state_restored_at": "2026-10-16T07:02:10Z",
            "token_status": "not-checked", "token_expires_at": None, "site_commit": None,
        })
        # Outside Actions, and without a receipt, the fields are simply null.
        bare = marker.build_marker("ctas-cloud.yml", {}, {}, generated_at="2026-10-16T07:02:11Z")
        self.assertIsNone(bare["run_url"])
        self.assertIsNone(bare["state_saved_at"])

    def test_marker_names_the_site_code_and_a_code_fallback(self):
        # RESILIENCE.md: the marker says which website commit built the release, and when a
        # release check blocked the current code and the code that produced the previous
        # release published instead, says so in code_fallback.
        main, good = "a" * 40, "b" * 40
        plain = marker.build_marker("ctas-cloud.yml", {}, ACTIONS_ENV, site_commit=main)
        self.assertEqual(plain["site_commit"], main)
        self.assertNotIn("code_fallback", plain)
        decision = {"action": "use-fallback", "main_commit": main, "release_commit": good,
                    "blocking": ["ctas.schemas"], "reason": "fallback-passed",
                    "blocking_detail": "status.schema.json 1 file(s)",
                    "explanation": "ctas.schemas failed with main"}
        fell_back = marker.build_marker("ctas-cloud.yml", {}, ACTIONS_ENV, site_commit=good, decision=decision)
        self.assertEqual(fell_back["site_commit"], good)
        self.assertEqual(fell_back["code_fallback"], {
            "failed_commit": main, "failed_check": "ctas.schemas", "failed_checks": ["ctas.schemas"],
            "detail": "status.schema.json 1 file(s)", "used_commit": good})
        self.assertEqual(marker.fallback_fields({"action": "block"}), {})
        with tempfile.TemporaryDirectory(prefix="cloud-run-marker-site-") as folder:
            subprocess.run(["git", "init", "-q", folder], check=True)
            subprocess.run(["git", "-C", folder, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-q", "--allow-empty", "-m", "site"], check=True)
            head = subprocess.run(["git", "-C", folder, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            self.assertEqual(marker.site_head(folder), head)
            (Path(folder) / "decision.json").write_text(json.dumps(decision))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/cloud_run_marker.py"), "--workflow", "ctas-cloud.yml",
                                     "--site", folder, "--fallback", str(Path(folder) / "decision.json"),
                                     "--output", str(Path(folder) / "cloud-run.json")],
                                    text=True, capture_output=True, env={"PATH": "/usr/bin:/bin"})
            self.assertEqual(result.returncode, 0, result.stderr)
            doc = json.loads((Path(folder) / "cloud-run.json").read_text())
            self.assertEqual(doc["site_commit"], head)
            self.assertEqual(doc["code_fallback"]["used_commit"], good)
            self.assertIn("(code fallback)", result.stdout)
        self.assertIsNone(marker.site_head(None))

    def test_cli_writes_the_marker_and_tolerates_a_missing_receipt(self):
        with tempfile.TemporaryDirectory(prefix="cloud-run-marker-") as folder:
            folder = Path(folder)
            (folder / "receipt.json").write_text(json.dumps(
                {"asset": "soc-20261016T061500Z.db.gz", "saved_at": "2026-10-16T06:15:00Z",
                 "restored_at": "2026-10-16T07:02:10Z"}))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/cloud_run_marker.py"),
                                     "--workflow", "ctas-cloud.yml", "--receipt", str(folder / "receipt.json"),
                                     "--output", str(folder / "cloud-run.json")],
                                    text=True, capture_output=True, env={"PATH": "/usr/bin:/bin", **ACTIONS_ENV})
            self.assertEqual(result.returncode, 0, result.stderr)
            doc = json.loads((folder / "cloud-run.json").read_text())
            self.assertEqual(doc["state_asset"], "soc-20261016T061500Z.db.gz")
            self.assertEqual(doc["run_id"], "18527")
            self.assertRegex(doc["generated_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertIn("state saved 2026-10-16T06:15:00Z (soc-20261016T061500Z.db.gz); key not-checked", result.stdout)
            result = subprocess.run([sys.executable, str(ROOT / "scripts/cloud_run_marker.py"),
                                     "--workflow", "ctas-cloud.yml", "--receipt", str(folder / "missing.json"),
                                     "--output", str(folder / "cloud-run.json")],
                                    text=True, capture_output=True, env={"PATH": "/usr/bin:/bin"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNone(json.loads((folder / "cloud-run.json").read_text())["state_saved_at"])
            self.assertIn("state saved unknown (no receipt)", result.stdout)

    def test_cloud_runs_write_the_marker_and_the_publishers_release_it(self):
        ctas_run = (ROOT / "scripts/ctas_cloud_run.sh").read_text()
        worlds_run = (ROOT / "scripts/worldsindex_cloud_run.sh").read_text()
        for text, workflow, variable in ((ctas_run, "ctas-cloud.yml", "CTAS_CLOUD_RUN_MARKER"),
                                         (worlds_run, "worldsindex-cloud.yml", "WORLDSINDEX_CLOUD_RUN_MARKER")):
            self.assertIn('STATE_RECEIPT="$WORK/state-restored.json"', text)
            self.assertIn(f'GH_TOKEN="$STATE_TOKEN" python3 "$SITE/scripts/cloud_run_marker.py" --workflow {workflow}', text)
            self.assertIn("--probe-token", text)
            self.assertIn(f'{variable}="$MARKER"', text)
            # the marker is never allowed to stop a run
            self.assertIn('|| { say "WARN  could not write the cloud-run marker"; MARKER=""; }', text)
        ctas_publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        self.assertIn("PUBLIC_FILES+=(ctas/data/cloud-run.json)", ctas_publisher)
        self.assertLess(ctas_publisher.index('git clean -fdqX -- ctas/data'), ctas_publisher.index("cloud-run.json"))
        worlds_publisher = (ROOT / "scripts/publish_worldsindex.sh").read_text()
        self.assertIn("PUBLIC_FILES+=(worldsindex/data/cloud-run.json)", worlds_publisher)
        # placed after the static test (every other file must be a manifest artifact) and
        # before the artifact guard (every file must be allowlisted)
        self.assertLess(worlds_publisher.index("scripts/test_worldsindex_static.py"), worlds_publisher.index("cloud-run.json"))
        self.assertLess(worlds_publisher.index("cloud-run.json"), worlds_publisher.index("# ---- Generic artifact guard"))
        self.assertIn('!= "cloud-run.json"', (ROOT / "scripts/test_worldsindex_static.py").read_text())
        for script in ("ctas_cloud_run.sh", "worldsindex_cloud_run.sh", "publish_ctas.sh", "publish_worldsindex.sh"):
            self.assertEqual(subprocess.run(["/bin/bash", "-n", str(ROOT / "scripts" / script)]).returncode, 0, script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
