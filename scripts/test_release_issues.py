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
    def test_an_advisory_warning_goes_to_the_daily_digest_never_to_an_issue_of_its_own(self):
        # 2026-11-24: 81 of 100 issues in two weeks were one advisory browser check.
        actions = ri.plan("deploy", [warn()], None, [], repo=REPO, run_url="https://example.test/run/1")
        self.assertEqual([(a["op"], a["label"]) for a in actions], [("create", "daily-digest")])
        self.assertEqual(actions[0]["title"], f"Daily digest {ri.today()}")
        self.assertIn("`ctas.test_ctas_browser` failed in the site deploy (advisory)", actions[0]["body"])
        self.assertIn("AssertionError: overflow 12px", actions[0]["body"])
        data = ri.parse_digest(actions[0]["body"])
        self.assertEqual([(r["kind"], r["key"], r["count"]) for r in data["items"]], [("advisory", "deploy:ctas.test_ctas_browser", 1)])
        # The same finding again today: the digest is edited (no comment, no e-mail), the count grows.
        existing = [{"number": 7, "title": actions[0]["title"], "body": actions[0]["body"], "label": "daily-digest"}]
        again = ri.plan("deploy", [warn()], None, existing, run_url="https://example.test/run/2")
        self.assertEqual([(a["op"], a["number"]) for a in again], [("edit", 7)])
        self.assertEqual(ri.parse_digest(again[0]["body"])["items"][0]["count"], 2)
        self.assertIn("2× between", again[0]["body"])
        # A different advisory check joins the same digest; nothing opens a check-warning issue.
        other = ri.plan("ctas-publish", [warn("ctas.test_ctas_feed")], None, existing)
        self.assertEqual([(a["op"], a["label"]) for a in other], [("edit", "daily-digest")])
        self.assertEqual(len(ri.parse_digest(other[0]["body"])["items"]), 2)
        # Nothing to report: no digest is created, and yesterday's digest is closed without a comment.
        self.assertEqual(ri.plan("deploy", [passed()], None, []), [])
        old = {"number": 3, "title": "Daily digest 2026-11-23", "label": "daily-digest",
               "body": ri.render_digest("2026-11-23", [ri.digest_item("note", "k", "t")])}
        closing = ri.plan("deploy", [passed()], None, [old])
        self.assertEqual([(a["op"], a["number"], a["body"]) for a in closing], [("close", 3, "")])

    def test_a_watch_mode_warning_opens_one_issue_edited_in_place(self):
        row = warn(cls="integrity")
        actions = ri.plan("deploy", [row], None, [], repo=REPO, run_url="https://example.test/run/1")
        self.assertEqual([(a["op"], a["label"]) for a in actions], [("create", "check-warning")])
        self.assertEqual(actions[0]["title"], "Check warning: ctas.test_ctas_browser")
        self.assertIn("did not block", actions[0]["body"])
        mark = ri.parse_marker(actions[0]["body"])
        self.assertEqual((mark["kind"], mark["key"], mark["scope"]), ("check-warning", "ctas.test_ctas_browser", "deploy"))
        existing = [{"number": 7, "title": "t", "body": actions[0]["body"], "last_marker": None}]
        self.assertEqual(ri.plan("deploy", [row], None, existing), [])
        changed = ri.plan("deploy", [warn(parts=["StatusPageTests.test_fits"], cls="integrity")], None, existing)
        self.assertEqual([(a["op"], a["number"]) for a in changed], [("edit", 7)])

    def test_digest_add_items_merge_and_the_lost_runner_kind_renders(self):
        item = ri.digest_item("lost-runner", "ctas:123", "run 123 of ctas-cloud.yml died with a lost runner", detail="phase publish", run_url="https://example.test/run/123")
        actions = ri.digest_actions([item], [])
        self.assertEqual([(a["op"], a["label"]) for a in actions], [("create", "daily-digest")])
        self.assertIn("### Runs that died with a lost runner", actions[0]["body"])
        self.assertIn("phase publish", actions[0]["body"])
        merged = ri.merge_digest_items(ri.parse_digest(actions[0]["body"])["items"], [ri.digest_item("advisory", "deploy:x", "x failed")])
        self.assertEqual([r["kind"] for r in merged], ["advisory", "lost-runner"])

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
                  {"op": "edit", "label": "check-warning", "number": 2, "body": "b"},
                  {"op": "close", "label": "fallback-active", "number": 3, "body": "c"},
                  {"op": "close", "label": "daily-digest", "number": 4, "body": ""}], "o/r", ["v4lue-secret"], runner=runner)
        flat = json.dumps(calls)
        self.assertNotIn(secret, flat)
        self.assertNotIn("v4lue-secret", flat)
        ops = [argv[1:3] for argv in calls]
        self.assertEqual(ops.count(["label", "create"]), len(ri.LABELS))
        self.assertIn(["issue", "create"], ops)
        self.assertIn(["issue", "edit"], ops)
        self.assertNotIn(["issue", "comment"], ops)
        closes = [argv for argv in calls if argv[1:3] == ["issue", "close"]]
        self.assertEqual(len(closes), 2)
        self.assertIn("--comment", closes[0])
        self.assertNotIn("--comment", closes[1])                        # a digest closes silently

    def test_every_label_description_fits_githubs_100_character_limit(self):
        # 2026-11-08: check-warning's 123-character description made "gh label create" fail
        # (silently), so every check-warning issue failed with "could not add label".
        for label, (color, description) in ri.LABELS.items():
            self.assertLessEqual(len(description), 100, label)
            self.assertRegex(color, r"^[0-9A-F]{6}$")

    def test_a_label_or_issue_failure_is_reported_and_the_other_actions_still_run(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv[1:3])
            if argv[1:3] == ["label", "create"] and argv[3] == "check-warning":
                return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 422: description is too long")
            if argv[1:3] == ["issue", "create"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="could not add label: 'check-warning' not found")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with self.assertRaises(RuntimeError) as caught:
            ri.apply([{"op": "create", "label": "check-warning", "title": "Check warning: a", "body": "x"},
                      {"op": "close", "label": "fallback-active", "number": 3, "body": "c"}], "o/r", [], runner=runner)
        message = str(caught.exception)
        self.assertIn("label check-warning", message)
        self.assertIn("description is too long", message)
        self.assertIn("could not add label", message)
        self.assertIn(["issue", "close"], calls)   # the later action still ran

    def test_a_body_in_which_a_credential_shape_survives_is_withheld_but_keeps_its_marker(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="https://github.com/o/r/issues/1\n" if argv[1:3] == ["issue", "create"] else "[]", stderr="")

        mark = ri.marker("check-warning", "ctas.export", "ctas-publish", "abc")
        body = f"Release check failed\n\n```\nremote said ghp_abc1234 refused; key Zq9xW2vB7nM4kL1pQ8rT\n```\n\n{mark}\n"
        results = [{"id": "ctas.export", "outcome": "warn", "class": "advisory", "parts": [],
                    "output_tail": ["remote said ghp_abc1234 refused", "x-access-token:abc"]}]
        planned = ri.plan("ctas-publish", results, None, [])           # today's digest, with the leaky tail
        ri.apply([{"op": "create", "label": "check-warning", "title": "Check warning: ctas.export", "body": body},
                  {"op": "edit", "label": "daily-digest", "number": 2, "body": planned[0]["body"]},
                  {"op": "create", "label": "release-blocked", "title": "Release blocked: ghp_abc1234", "body": "ok"}],
                 "o/r", ["Zq9xW2vB7nM4kL1pQ8rT"], runner=runner)
        flat = json.dumps(calls)
        for leftover in ("ghp_abc1234", "Zq9xW2vB7nM4kL1pQ8rT", "x-access-token:abc"):
            self.assertNotIn(leftover, flat)
        posted = [argv[argv.index("--body") + 1] for argv in calls if "--body" in argv]
        self.assertTrue(posted[0].startswith(ri.WITHHELD))
        self.assertIn(mark, posted[0])                                   # the next run still finds the issue
        self.assertTrue(posted[1].startswith(ri.WITHHELD))
        digest = ri.parse_digest(posted[1])                              # the digest keeps its items, without details
        self.assertEqual([(r["key"], r["detail"]) for r in digest["items"]], [("ctas-publish:ctas.export", "")])
        titles = [argv[argv.index("--title") + 1] for argv in calls if "--title" in argv]
        self.assertIn("title withheld", titles[1])
        self.assertEqual(posted[2], "ok")                                # clean bodies are posted as they are

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
