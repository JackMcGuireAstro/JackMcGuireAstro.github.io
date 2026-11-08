#!/usr/bin/env python3
"""scripts/verify_live.sh against a local stand-in for GitHub Pages and the data origin.

The deploy's live check (verify-live job) failed once on 2026-11-08 (run 42479503762) with no
record of which file disagreed. These tests pin the rewritten check: it passes when everything
matches, names each mismatch with its HTTP status and writes it to $GITHUB_OUTPUT for the run
report, treats a chunk the data origin answers without CORS as a mismatch (the browser could not
read it), and reports "verified with a warning" only when the data origin rate-limits (HTTP 429)
every read of the chunk while everything else matches.
"""
from __future__ import annotations

import functools
import gzip
import hashlib
import http.server
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_live.sh"
CHUNK_PATH = "ctas/data/candidate-chunks/dd2.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Handler(http.server.SimpleHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *args):  # quiet
        pass

    def end_headers(self):
        if self.path.startswith("/origin/") and self.server.mode != "no-cors":  # type: ignore[attr-defined]
            self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/origin/") and self.server.mode == "429":  # type: ignore[attr-defined]
            self.send_response(429)
            self.end_headers()
            return
        self.path = self.path.split("?", 1)[0]
        return super().do_GET()


class VerifyLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="verify-live-"))
        site = cls.tmp / "site"
        (site / "worldsindex/data").mkdir(parents=True)
        (site / "ctas/data").mkdir(parents=True)
        cls.manifest = b'{"objectCount": 3}\n'
        cls.index = gzip.compress(b'{"objects": []}', mtime=0)
        cls.status = b'{"last_successful_update": "2026-11-08T13:01:36Z"}\n'
        cls.chunk = b'{"bucket": "dd2", "candidates": []}\n'
        (site / "worldsindex/data/manifest.json").write_bytes(cls.manifest)
        (site / "worldsindex/data/catalog-index.json.gz").write_bytes(cls.index)
        (site / "ctas/data/status.json").write_bytes(cls.status)
        origin = site / "origin" / "abc123"
        (origin / "ctas/data/candidate-chunks").mkdir(parents=True)
        (origin / CHUNK_PATH).write_bytes(cls.chunk)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(site)))
        cls.server.mode = "ok"
        cls.port = cls.server.server_address[1]
        base = f"http://127.0.0.1:{cls.port}/"
        (site / "ctas/delivery.json").write_text(json.dumps({
            "schema": "ctas.delivery@1.0.0", "candidate_chunk_encoding": "gzip",
            "candidate_chunk_origin": {"base_url": base + "origin/abc123/"},
        }))
        cls.base = base
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_check(self, mode="ok", **overrides):
        self.server.mode = mode
        out = self.tmp / f"output-{mode}-{len(overrides)}"
        out.write_text("")
        env = {**os.environ, "PAGE_URL": self.base, "VERIFY_ATTEMPTS": "2", "VERIFY_SLEEP": "0",
               "VERIFY_RUN_ID": "1", "VERIFY_ORIGIN_PREFIX": self.base, "GITHUB_OUTPUT": str(out),
               "WI_MANIFEST_SHA": sha(self.manifest), "WI_INDEX_SHA": sha(self.index),
               "CTAS_STATUS_SHA": sha(self.status), "CTAS_CHUNK_PATH": CHUNK_PATH, "CTAS_CHUNK_SHA": sha(self.chunk),
               "WI_COMMIT": "b3121f6dc0", "CTAS_COMMIT": "de3f5da3da", "RUNNER_TEMP": str(self.tmp)}
        env.update(overrides)
        done = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        return done, out.read_text()

    def test_everything_matches(self):
        done, output = self.run_check()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("live site verified", done.stdout)
        self.assertIn("detail=verified on attempt 1", output)

    def test_a_mismatch_is_named_with_its_http_status_for_the_run_report(self):
        done, output = self.run_check(CTAS_STATUS_SHA="0" * 64)
        self.assertEqual(done.returncode, 1)
        self.assertIn("CTAS-status(HTTP 200)", done.stderr)
        self.assertIn("detail=never matched after 2 attempts: CTAS-status(HTTP 200)", output)

    def test_a_missing_file_reports_its_404(self):
        done, output = self.run_check(CTAS_CHUNK_PATH="ctas/data/candidate-chunks/nope.json")
        self.assertEqual(done.returncode, 1)
        self.assertIn("CTAS-chunk(nope.json, HTTP 404 from the data origin)", output)

    def test_a_chunk_without_cors_is_a_mismatch(self):
        done, output = self.run_check(mode="no-cors")
        self.assertEqual(done.returncode, 1)
        self.assertIn("HTTP 200-no-cors", output)

    def test_a_rate_limited_data_origin_alone_is_a_warning(self):
        done, output = self.run_check(mode="429")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("verified with a warning", done.stdout)
        self.assertIn("detail=verified with a warning: chunk origin rate-limited (HTTP 429)", output)

    def test_rate_limit_plus_another_mismatch_still_fails(self):
        done, output = self.run_check(mode="429", WI_MANIFEST_SHA="0" * 64)
        self.assertEqual(done.returncode, 1)
        self.assertIn("WorldsIndex-manifest", output)

    def test_the_workflow_runs_this_script_and_reports_its_detail(self):
        workflow = (ROOT / ".github/workflows/worldsindex-release.yml").read_text()
        self.assertIn("run: bash scripts/verify_live.sh", workflow)
        self.assertIn("detail: ${{ steps.compare.outputs.detail }}", workflow)
        self.assertIn("VERIFY_DETAIL: ${{ needs.verify-live.outputs.detail }}", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
