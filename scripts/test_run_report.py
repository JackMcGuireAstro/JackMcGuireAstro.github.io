#!/usr/bin/env python3
"""The run-report sanitizer (planted secrets), the refusal to write a report in which a
credential shape survives, the report document, and the run-reports branch writer."""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_report as rr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# Shaped like real credentials, but made up for the test.
GHP = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
GHO = "gho_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"
GHS = "ghs_" + "abcdefghijklmnopqrstuvwxyz012345"
PAT = "github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123456789"
AWS_ID = "AKIA" + "IOSFODNN7EXAMPLE"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCYEXAMPLEKEY"
CF_TOKEN = "Y9xk2" + "q_LmN0pQrStUvWxYz1234567890abcdEfGh"
CF_GLOBAL = "1234567890abcdef" + "1234567890abcdef12345"


class SanitizerTests(unittest.TestCase):
    def test_named_secret_values_and_dotenv_values_are_removed(self):
        env = {"STATE_TOKEN": "state-key-value-123", "BACKEND_ENV": "TNS_API_KEY=tns-secret-456\nAAVSO=\"aavso-secret-789\"\n# c=no\n",
               "EMPTY": "", "SHORT": "abc"}
        values = rr.env_secret_values("STATE_TOKEN BACKEND_ENV EMPTY SHORT MISSING", env)
        text = "restore with state-key-value-123 failed; tns-secret-456 and aavso-secret-789 leaked; abc stays"
        clean = rr.sanitize(text, values)
        for secret in ("state-key-value-123", "tns-secret-456", "aavso-secret-789"):
            self.assertNotIn(secret, clean)
        self.assertIn("abc stays", clean)
        # every listed variable is read when no names are given
        self.assertIn("state-key-value-123", rr.env_secret_values(None, env))
        with tempfile.TemporaryDirectory(prefix="sanitize-") as folder:
            dotenv = Path(folder) / ".env"
            dotenv.write_text("# settings\nADS_API_TOKEN='ads-token-0001'\nEMPTY=\nX=ab\n")
            self.assertEqual(rr.dotenv_values(dotenv), ["ads-token-0001"])
            self.assertEqual(rr.dotenv_values(Path(folder) / "missing"), [])

    def test_planted_secrets_of_every_known_shape_are_removed(self):
        token = "plain-deploy-token-0123456789"
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        lines = [
            f"remote: {GHP} rejected", f"token={GHO}", f"installation {GHS}", f"pat {PAT}",
            "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46Z2hzX3NlY3JldA==",
            "Authorization: Bearer abc.def.ghijklmnopqrstuv",
            "curl -H 'Authorization: token 0123456789abcdef0123'",
            "fatal: unable to access 'https://x-access-token:ghs_inner@github.com/o/r.git/'",
            "https://user:hunter2-password@example.org/path?q=1",
            "proxy http://alice@proxy.local:8080/",
            "bearer zzzzzzzzzzzzzzzzzzzzzz",
            f"aws: {AWS_ID} / aws_secret_access_key = {AWS_SECRET}",
            f"CLOUDFLARE_API_TOKEN={CF_TOKEN}", f"X-Auth-Key: {CF_GLOBAL}",
            f"git -c http.extraheader='AUTHORIZATION: basic {basic}'", f"encoded {basic} alone",
            "TNS_API_KEY=abcdef123456 SOME_SECRET=s3cr3t-v4lue DB_PASSWORD=pw-123456",
            "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----",
        ]
        clean = rr.sanitize("\n".join(lines), [token])
        for secret in (GHP, GHO, GHS, PAT, "eC1hY2Nlc3MtdG9rZW46Z2hzX3NlY3JldA==", "abc.def.ghijklmnopqrstuv",
                       "0123456789abcdef0123", "ghs_inner", "hunter2-password", "alice@", "zzzzzzzzzzzzzzzzzzzzzz",
                       AWS_ID, AWS_SECRET, CF_TOKEN, CF_GLOBAL, basic, token, "abcdef123456", "s3cr3t-v4lue", "pw-123456",
                       "b3BlbnNzaC1rZXktdjEAAAAA"):
            self.assertNotIn(secret, clean, secret)
        self.assertIn("https://***@example.org/path?q=1", clean)
        self.assertIn("AUTHORIZATION: ***", clean)
        self.assertEqual(rr.leaks(clean, [token]), [])
        # ordinary text survives untouched
        plain = ("phase  export took 412s\nFAIL  check ctas.schemas failed (candidate-chunk.schema.json)\n"
                 "https://github.com/o/r/actions/runs/1\ncommit e8cc0f9f3c742b0daac9284c24e0ce58a6768c55\ntoken_status: ok")
        self.assertEqual(rr.sanitize(plain), plain)
        self.assertEqual(rr.leaks(plain), [])
        self.assertEqual(rr.sanitize(""), "")
        self.assertEqual(rr.sanitize_tree({"a": [f"x {GHP}", 3, None]}, []), {"a": ["x ***", 3, None]})

    def test_query_string_keys_and_percent_encoded_values_are_removed(self):
        # A settings value with URL-special characters appears percent-encoded in a request
        # URL; a key the run does not know appears as a query parameter (review 2026-10-23).
        value = "s3cr3t+/val=ue9876"
        lines = [
            "FAIL  GET https://example.org/q?key=s3cr3t%2B%2Fval%3Due9876 -> 403",
            "FAIL  GET https://example.org/q?key=s3cr3t%2B%2Fval%3Due9876&x=1 (form s3cr3t%2B%2Fval%3Due9876)",
            "FAIL  GET https://example.org/q?a=1&token=unknownTok3n1234567890 -> 403",
            "FAIL  GET https://www.wis-tns.org/api/get?api_key=Zq9xW2vB7nM4kL1pQ8rT&objname=2026abc",
            "FAIL  https://bucket.s3.amazonaws.com/f?X-Amz-Credential=AKIDEXAMPLE%2F2026&X-Amz-Signature=abcdef0123456789",
        ]
        clean = rr.sanitize("\n".join(lines), [value])
        for secret in ("s3cr3t%2B%2Fval%3Due9876", "unknownTok3n1234567890", "Zq9xW2vB7nM4kL1pQ8rT", "abcdef0123456789",
                       "AKIDEXAMPLE"):
            self.assertNotIn(secret, clean, secret)
        self.assertIn("api_key=***&objname=2026abc", clean)
        self.assertIn("?a=1&token=***", clean)
        # ordinary query strings stay readable
        plain = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits?hips=CDS%2FP%2FDSS2%2Fcolor&width=96&ra=1.5"
        self.assertEqual(rr.sanitize(plain), plain)

    def test_a_surviving_credential_shape_refuses_the_report(self):
        # A shape the redaction does not know but the final scan does (a too-short ghp_ token,
        # an Authorization header whose value is cut oddly) refuses the whole report.
        for leftover in ("ghp_abc1234", "AKIA1234ABCD", "x-access-token:abc"):
            self.assertTrue(rr.leaks(f"line {leftover}"), leftover)
        report = {"lines": ["ok"], "sanitized": True, "note": "ghp_" + "short1"}
        with self.assertRaises(rr.LeakError):
            rr.checked_json(report, [])
        with self.assertRaises(rr.LeakError):
            rr.checked_json({"lines": ["value my-planted-secret-1"]}, ["my-planted-secret-1"])
        self.assertIn('"lines"', rr.checked_json({"lines": ["fine"]}, ["my-planted-secret-1"]))

    def test_the_cli_reads_secret_names_never_values(self):
        done = subprocess.run([sys.executable, str(ROOT / "scripts/run_report.py"), "sanitize", "--secret-env", "MY_SECRET"],
                              input="value my-secret-value-1 here and state-key-99", text=True, capture_output=True,
                              env={**os.environ, "MY_SECRET": "my-secret-value-1", "STATE_TOKEN": "state-key-99"})
        self.assertEqual(done.stdout, "value *** here and ***")

    def test_every_secret_bearing_variable_the_workflows_set_is_scrubbed(self):
        """Read every workflow file: each env variable assigned from ${{ secrets.* }} or the
        workflow token must be in SECRET_ENV_NAMES, and each cloud runner names the ones its
        step receives."""
        assigned = set()
        for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
            for name, value in re.findall(r"^\s+([A-Z][A-Z0-9_]*):\s*(\$\{\{.*?\}\})", workflow.read_text(), re.MULTILINE):
                if "secrets." in value or "github.token" in value:
                    assigned.add(name)
        self.assertTrue({"STATE_TOKEN", "DEPLOY_TOKEN", "GH_TOKEN", "BACKEND_ENV", "BUILDER_ENV"} <= assigned, assigned)
        self.assertEqual(sorted(assigned - set(rr.SECRET_ENV_NAMES)), [])
        for runner, names in (("ctas_cloud_run.sh", ("STATE_TOKEN", "DEPLOY_TOKEN", "BACKEND_ENV", "AAVSO_AID_API_TOKEN_OVERRIDE")),
                              ("worldsindex_cloud_run.sh", ("STATE_TOKEN", "DEPLOY_TOKEN", "BUILDER_ENV"))):
            line = next(l for l in (ROOT / "scripts" / runner).read_text().splitlines() if l.startswith("SECRET_ENV="))
            for name in names:
                self.assertIn(name, line, runner)


class ReportTests(unittest.TestCase):
    def test_report_has_ids_phases_evidence_lines_and_no_secrets(self):
        with tempfile.TemporaryDirectory(prefix="run-report-") as folder:
            log = Path(folder) / "publish.log"
            log.write_text("2026-10-22 10:00:00 UTC  phase  freeze-database took 40s\n"
                           "2026-10-22 10:05:00 UTC  phase  export took 300s\n"
                           "WARN  check ctas.test_ctas_browser failed: advisory, not blocking\n"
                           f"2026-10-22 10:06:00 UTC  FAIL  scripts/test_ctas_static.py failed; token {GHP}\n"
                           "  candidate-chunk.schema.json: Schema validation errors were encountered.\n"
                           "a line that only mentions FAILURES in passing\n")
            rows = [
                {"id": "ctas.test_ctas_browser", "outcome": "warn", "class": "advisory", "mode": "warn", "parts": [],
                 "output_tail": ["layout overflow"]},
                {"id": "ctas.export", "scope": "ctas-publish", "outcome": "block", "class": "integrity", "mode": "block",
                 "reason": "promoted", "parts": [{"part": "x", "class": "integrity"}],
                 "output_tail": [f"line {i} secret-value-xyz" for i in range(70)]},
                {"id": "ctas.schemas:status.schema.json", "family": "ctas.schemas", "outcome": "block"},
                {"id": "ctas.links", "outcome": "pass"},
            ]
            decision = {"action": "try-fallback", "main_commit": "a" * 40, "release_commit": "b" * 40}
            report = rr.build_report(workflow="ctas-cloud.yml", outcome="blocked", logs=[str(log), str(Path(folder) / "none.log")],
                                     results=rows, decision=decision, commit="a" * 40, secrets=["secret-value-xyz"],
                                     environ={"GITHUB_RUN_ID": "123", "GITHUB_REPOSITORY": "o/r", "GITHUB_SERVER_URL": "https://github.com"},
                                     now=dt.datetime(2026, 10, 22, 10, 7, tzinfo=dt.timezone.utc))
        self.assertEqual(report["schema"], "run-report@2")
        self.assertEqual(report["run_url"], "https://github.com/o/r/actions/runs/123")
        self.assertEqual([p["phase"] for p in report["phases"]], ["freeze-database", "export"])
        self.assertEqual(report["failing_checks"], ["ctas.export"])
        self.assertEqual(report["warning_checks"], ["ctas.test_ctas_browser"])
        self.assertEqual(report["failing_phase"], "after export")
        self.assertEqual(len(report["lines"]), 5)       # two phases, the WARN, the FAIL, the validator line
        self.assertTrue(any("candidate-chunk.schema.json: Schema validation" in line for line in report["lines"]))
        self.assertEqual(report["failing_check"]["id"], "ctas.export")
        self.assertEqual(len(report["failing_check"]["output_tail"]), 60)
        self.assertEqual(report["check_counts"], {"pass": 1, "warn": 1, "block": 1, "skip": 0})
        self.assertEqual(report["decision"]["action"], "try-fallback")
        text = rr.checked_json(report, ["secret-value-xyz"])
        self.assertNotIn(GHP, text)
        self.assertNotIn("secret-value-xyz", text)
        self.assertTrue(report["sanitized"])
        self.assertEqual(rr.report_paths(report), ("reports/ctas-cloud/123.json", "reports/ctas-cloud/latest.json"))

    def test_at_most_200_evidence_lines(self):
        with tempfile.TemporaryDirectory(prefix="run-report-") as folder:
            log = Path(folder) / "publish.log"
            log.write_text("".join(f"FAIL  check number {i}\n" for i in range(500)))
            rows = [{"id": "ctas.export", "outcome": "block", "output_tail": [f"t{i}" for i in range(60)]}]
            report = rr.build_report(workflow="ctas-cloud.yml", outcome="failed", logs=[str(log)], results=rows, environ={})
        self.assertEqual(len(report["lines"]) + len(report["failing_check"]["output_tail"]), 200)

    def test_prune_keeps_the_newest_50_per_workflow(self):
        paths = [f"reports/ctas-cloud/{i}.json" for i in range(1, 61)] + \
                [f"reports/worldsindex-release/{100 + i}.json" for i in range(5)] + \
                ["reports/ctas-cloud/latest.json", "README.md", "history/deploy.json"]
        removed = rr.prune(paths)
        self.assertEqual(removed, sorted(f"reports/ctas-cloud/{i}.json" for i in range(1, 11)))
        self.assertEqual(rr.prune(paths, keep=3)[-1], "reports/worldsindex-release/101.json")
        self.assertNotIn("reports/ctas-cloud/latest.json", rr.prune(paths, keep=1))


class BranchTests(unittest.TestCase):
    """The run-reports branch against a local bare remote: created, extended, pruned, raced."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="run-reports-branch-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        self.repo = root / "site"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin", str(self.remote)], check=True)

    def tree(self):
        return subprocess.run(["git", "-C", str(self.remote), "ls-tree", "-r", "--name-only", "run-reports"],
                              capture_output=True, text=True, check=True).stdout.split()

    def test_reports_accumulate_in_one_parentless_commit_and_are_pruned(self):
        for run in range(1, 6):
            rr.push_files(str(self.repo), "run-reports", {f"reports/ctas-cloud/{run}.json": "{}\n",
                                                          "reports/ctas-cloud/latest.json": json.dumps({"run": run})}, "test", keep=3)
        files = self.tree()
        self.assertEqual(sorted(p for p in files if re.fullmatch(r"reports/ctas-cloud/\d+\.json", p)),
                         ["reports/ctas-cloud/3.json", "reports/ctas-cloud/4.json", "reports/ctas-cloud/5.json"])
        self.assertIn("README.md", files)
        latest = subprocess.run(["git", "-C", str(self.remote), "show", "run-reports:reports/ctas-cloud/latest.json"],
                                capture_output=True, text=True).stdout
        self.assertEqual(json.loads(latest), {"run": 5})
        parents = subprocess.run(["git", "-C", str(self.remote), "log", "--format=%P", "-1", "run-reports"],
                                 capture_output=True, text=True).stdout.strip()
        self.assertEqual(parents, "")

    def test_a_concurrent_writer_is_retried_not_lost(self):
        rr.push_files(str(self.repo), "run-reports", {"reports/a/1.json": "{}"}, "first")
        other = Path(self.tmp.name) / "other"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(other)], check=True)
        original = rr.git
        calls = {"n": 0}

        def racing(repo, *args, **kwargs):
            # just before our first push, another writer replaces the branch
            if args and args[0] == "commit-tree" and calls["n"] == 0:
                calls["n"] += 1
                rr.push_files(str(other), "run-reports", {"reports/b/2.json": "{}"}, "other writer")
            return original(repo, *args, **kwargs)

        rr.git = racing
        try:
            rr.push_files(str(self.repo), "run-reports", {"reports/c/3.json": "{}"}, "ours")
        finally:
            rr.git = original
        files = self.tree()
        for name in ("reports/a/1.json", "reports/b/2.json", "reports/c/3.json"):
            self.assertIn(name, files)

    def test_the_cli_pushes_only_sanitized_reports_and_refuses_leaks(self):
        with tempfile.TemporaryDirectory(prefix="run-report-cli-") as folder:
            folder = Path(folder)
            script = str(ROOT / "scripts/run_report.py")
            (folder / "raw.json").write_text(json.dumps({"workflow": "x.yml", "run_id": "1"}))
            refused = subprocess.run([sys.executable, script, "push", "--report", str(folder / "raw.json"),
                                      "--repo-dir", str(self.repo)], capture_output=True, text=True)
            self.assertEqual(refused.returncode, 2)
            env = {**os.environ, "GITHUB_RUN_ID": "77"}
            built = subprocess.run([sys.executable, script, "build", "--workflow", "worldsindex-release.yml",
                                    "--outcome", "failed", "--out", str(folder / "report.json")], capture_output=True, text=True, env=env)
            self.assertEqual(built.returncode, 0, built.stderr)
            (folder / "history.json").write_text('{"schema": "check-history@1"}\n')
            pushed = subprocess.run([sys.executable, script, "push", "--report", str(folder / "report.json"),
                                     "--repo-dir", str(self.repo), "--extra", f"history/deploy.json={folder / 'history.json'}"],
                                    capture_output=True, text=True)
            self.assertEqual(pushed.returncode, 0, pushed.stderr)
            # A planted secret value that the report would carry is masked; one that reappears
            # after the build (a hand-edited report) refuses the push.
            (folder / "leaky.log").write_text("FAIL  could not push with PLANTEDsecret1234567\n")
            leaky = subprocess.run([sys.executable, script, "build", "--workflow", "ctas-cloud.yml", "--outcome", "failed",
                                    "--log", str(folder / "leaky.log"), "--out", str(folder / "leaky.json")],
                                   capture_output=True, text=True, env={**env, "DEPLOY_TOKEN": "PLANTEDsecret1234567"})
            self.assertEqual(leaky.returncode, 0, leaky.stderr)
            self.assertNotIn("PLANTEDsecret1234567", (folder / "leaky.json").read_text())
            doc = json.loads((folder / "leaky.json").read_text())
            doc["note"] = "PLANTEDsecret1234567"
            (folder / "leaky.json").write_text(json.dumps(doc))
            again = subprocess.run([sys.executable, script, "push", "--report", str(folder / "leaky.json"), "--repo-dir", str(self.repo)],
                                   capture_output=True, text=True, env={**env, "DEPLOY_TOKEN": "PLANTEDsecret1234567"})
            self.assertEqual(again.returncode, 2)
            self.assertIn("refusing", again.stderr)
            self.assertNotIn("PLANTEDsecret1234567", again.stderr)
        files = self.tree()
        self.assertIn("reports/worldsindex-release/latest.json", files)
        self.assertIn("reports/worldsindex-release/77.json", files)
        self.assertIn("history/deploy.json", files)
        self.assertNotIn("reports/ctas-cloud/latest.json", files)


if __name__ == "__main__":
    unittest.main(verbosity=2)
