#!/usr/bin/env python3
"""The release-check issues: one check-warning issue per check, one fallback-active and one
release-blocked issue per scope, opened, updated only on change, closed on recovery."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_issues as ri  # noqa: E402

MAIN, GOOD = "a" * 40, "b" * 40
REPO = "JackMcGuireAstro/JackMcGuireAstro.github.io"


def warn(check_id="ctas.test_ctas_browser", parts=(), cls="advisory"):
    return {"id": check_id, "outcome": "warn", "class": cls, "mode": "warn", "reason": "advisory: never blocks",
            "parts": [{"part": p, "class": cls} for p in parts], "output_tail": ["AssertionError: overflow 12px"]}


def passed(check_id="ctas.test_ctas_browser"):
    return {"id": check_id, "outcome": "pass", "class": "advisory", "parts": []}


def issue(number, kind, key, scope=None, fp="x"):
    return {"number": number, "title": "t", "body": ri.marker(kind, key, scope, fp), "last_marker": None}


class PlanTests(unittest.TestCase):
    def test_a_warning_opens_one_issue_and_repeats_are_quiet(self):
        actions = ri.plan("deploy", [warn()], None, [], repo=REPO, run_url="https://example.test/run/1")
        self.assertEqual([(a["op"], a["label"]) for a in actions], [("create", "check-warning")])
        self.assertEqual(actions[0]["title"], "Check warning: ctas.test_ctas_browser")
        self.assertIn("did not block", actions[0]["body"])
        self.assertIn("advisory: it never blocks", actions[0]["body"])
        self.assertIn("AssertionError: overflow 12px", actions[0]["body"])
        mark = ri.parse_marker(actions[0]["body"])
        self.assertEqual((mark["kind"], mark["key"], mark["scope"]), ("check-warning", "ctas.test_ctas_browser", "deploy"))
        existing = [{"number": 7, "title": "t", "body": actions[0]["body"], "last_marker": None}]
        self.assertEqual(ri.plan("deploy", [warn()], None, existing), [])
        changed = ri.plan("deploy", [warn(parts=["StatusPageTests.test_fits"])], None, existing)
        self.assertEqual([(a["op"], a["number"]) for a in changed], [("comment", 7)])

    def test_a_pass_closes_the_warning_in_the_same_scope_only(self):
        open_in_deploy = [issue(9, "check-warning", "ctas.test_ctas_browser", "deploy")]
        self.assertEqual([(a["op"], a["number"]) for a in ri.plan("deploy", [passed()], None, open_in_deploy)], [("close", 9)])
        self.assertEqual(ri.plan("ctas-publish", [passed()], None, open_in_deploy), [])
        self.assertEqual(ri.plan("deploy", [passed("ctas.other")], None, open_in_deploy), [])

    def test_watch_mode_integrity_warnings_say_they_will_block(self):
        row = warn("ctas.schemas", cls="integrity")
        row["reason"] = "watch since 2026-10-20: 1/3 consecutive real-release passes"
        body = ri.plan("ctas-publish", [row], None, [])[0]["body"]
        self.assertIn("still in watch mode", body)
        self.assertIn("it will block once promoted", body)

    def test_fallback_active_lifecycle(self):
        blocking = {"id": "ctas.export", "outcome": "block", "class": "integrity", "mode": "block", "reason": "promoted",
                    "parts": [], "output_tail": ["KeyError: 'links'"]}
        decision = {"action": "use-fallback", "main_commit": MAIN, "release_commit": GOOD, "blocking_detail": "KeyError in export",
                    "blocking": ["ctas.export"], "explanation": "the same data passed with the code that produced the published release"}
        actions = ri.plan("ctas-publish", [blocking], decision, [], repo=REPO)
        created = next(a for a in actions if a["label"] == "fallback-active")
        self.assertEqual(created["op"], "create")
        self.assertIn(f"https://github.com/{REPO}/compare/{GOOD}...{MAIN}", created["body"])
        self.assertIn("`ctas.export`", created["body"])
        self.assertIn("KeyError: 'links'", created["body"])
        self.assertEqual("CTAS published with fallback code (bbbbbbb)", created["title"])
        self.assertIn("detail: KeyError in export", created["body"])
        for scope, title in (("worldsindex-publish", "WorldsIndex published with fallback code"), ("deploy", "Site deployed with fallback code")):
            made = [a for a in ri.plan(scope, [blocking], decision, [], repo=REPO) if a["label"] == "fallback-active"]
            self.assertTrue(made[0]["title"].startswith(title), made[0]["title"])
        # the deploy's landing window (main younger than 3 h, the CTAS release's code deployed) opens nothing
        landing = dict(decision, landing_window=True)
        self.assertFalse([a for a in ri.plan("deploy", [blocking], landing, [], repo=REPO) if a["label"] == "fallback-active"])
        # sub-check rows (one per schema file) never open their own issue
        sub = dict(warn("ctas.schemas:candidate-chunk.schema.json"), family="ctas.schemas")
        self.assertEqual(ri.plan("ctas-publish", [sub], None, []), [])
        same = [{"number": 3, "title": "t", "body": created["body"], "last_marker": None}]
        self.assertFalse([a for a in ri.plan("ctas-publish", [blocking], decision, same, repo=REPO) if a["label"] == "fallback-active"])
        closed = ri.plan("ctas-publish", [], {"action": "proceed", "main_commit": MAIN}, same)
        self.assertEqual([(a["op"], a["label"], a["number"]) for a in closed], [("close", "fallback-active", 3)])

    def test_release_blocked_lifecycle_and_quiet_reasons(self):
        decision = {"action": "block", "reason": "data-integrity", "main_commit": MAIN, "release_commit": GOOD,
                    "blocking": ["ctas.gate.catalog-population"], "explanation": "the fallback code reports the same failure"}
        actions = ri.plan("ctas-publish", [], decision, [], repo=REPO, mention="@someone")
        self.assertEqual([(a["op"], a["label"]) for a in actions], [("create", "release-blocked")])
        self.assertIn("@someone", actions[0]["body"])
        self.assertIn("data-integrity", actions[0]["body"])
        quiet = dict(decision, reason="error-outside-checks", blocking=[])
        self.assertEqual(ri.plan("ctas-publish", [], quiet, []), [])
        existing = [{"number": 5, "title": "t", "body": actions[0]["body"], "last_marker": None}]
        for action in ("proceed", "use-fallback"):
            closing = [a for a in ri.plan("ctas-publish", [], {"action": action, "main_commit": MAIN, "release_commit": GOOD,
                                                                 "blocking": []}, existing) if a["label"] == "release-blocked"]
            self.assertEqual([(a["op"], a["number"]) for a in closing], [("close", 5)])

    def test_apply_calls_the_github_cli_with_sanitized_bodies(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="https://github.com/o/r/issues/1\n" if argv[1:3] == ["issue", "create"] else "[]", stderr="")

        secret = "ghp_" + "x" * 36
        ri.apply([{"op": "create", "label": "check-warning", "title": "Check warning: a", "body": f"leak {secret} and v4lue-secret"},
                  {"op": "comment", "label": "check-warning", "number": 2, "body": "b"},
                  {"op": "close", "label": "fallback-active", "number": 3, "body": "c"}], "o/r", ["v4lue-secret"], runner=runner)
        flat = json.dumps(calls)
        self.assertNotIn(secret, flat)
        self.assertNotIn("v4lue-secret", flat)
        ops = [argv[1:3] for argv in calls]
        self.assertEqual(ops.count(["label", "create"]), 3)
        self.assertIn(["issue", "create"], ops)
        self.assertIn(["issue", "comment"], ops)
        self.assertIn(["issue", "close"], ops)

    def test_open_issues_reads_the_newest_marker_from_comments(self):
        def runner(argv, **kwargs):
            if argv[1:3] == ["issue", "list"]:
                label = argv[argv.index("--label") + 1]
                rows = [{"number": 4, "title": "t", "body": ri.marker("check-warning", "x.y", "deploy", "old")}] if label == "check-warning" else []
                return SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr="")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"comments": [{"body": ri.marker("check-warning", "x.y", "deploy", "new")}]}), stderr="")

        issues = ri.open_issues("o/r", runner=runner)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["last_marker"]["fp"], "new")


if __name__ == "__main__":
    unittest.main(verbosity=2)
