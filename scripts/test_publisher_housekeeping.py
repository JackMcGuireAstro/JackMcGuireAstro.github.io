#!/usr/bin/env python3
"""Exercise publisher_housekeeping.sh against a fixture origin and runtime clone."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/publisher_housekeeping.sh"
DAY = 86400


def run(args, cwd, env=None, check=True):
    merged = dict(os.environ, GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="maintenance.auto",
                  GIT_CONFIG_VALUE_0="false", GIT_CONFIG_KEY_1="gc.auto", GIT_CONFIG_VALUE_1="0")
    merged.update(env or {})
    result = subprocess.run(args, cwd=cwd, env=merged, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError(f"{args} failed ({result.returncode}): {result.stderr}")
    return result


class HousekeepingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="publisher-housekeeping-")
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name).resolve()
        self.origin = base / "origin.git"
        author = base / "author"
        run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], base)
        run(["git", "init", "-q", "-b", "main", str(author)], base)
        for key, value in (("user.name", "fixture"), ("user.email", "fixture@example.invalid")):
            run(["git", "config", key, value], author)
        (author / "ctas/data").mkdir(parents=True)
        for index in range(40):
            (author / "ctas/data/status.json").write_text(f'{{"release": {index}, "pad": "{"x" * 4000}{index}"}}')
            run(["git", "add", "--all"], author)
            run(["git", "commit", "-qm", f"CTAS data: release {index}"], author)
        run(["git", "remote", "add", "origin", self.origin.as_uri()], author)
        run(["git", "push", "-q", "origin", "main"], author)
        self.author = author
        self.site = base / "site"
        run(["git", "clone", "-q", self.origin.as_uri(), str(self.site)], base)
        for key, value in (("user.name", "publisher"), ("user.email", "publisher@example.invalid")):
            run(["git", "config", key, value], self.site)
        self.stamp = base / "last-housekeeping"

    def git(self, *args):
        return run(["git", *args], self.site).stdout.strip()

    def maintain(self, **env):
        settings = {"PUBLISHER_HISTORY_DEPTH": "10"}
        settings.update(env)
        return run(["bash", str(SCRIPT), "maintain", "ctas", "main", str(self.stamp)], self.site, settings)

    def recovery_ref(self, name, age_days):
        stamp = str(int(time.time() - age_days * DAY)) + " +0000"
        tree = self.git("rev-parse", "HEAD^{tree}")
        commit = run(["git", "commit-tree", tree, "-p", "HEAD", "-m", "CTAS generated recovery"], self.site,
                     {"GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp}).stdout.strip()
        self.git("update-ref", f"refs/ctas-recovery/{name}", commit)

    def test_trims_history_prunes_old_recovery_refs_and_clears_generated_stashes(self):
        self.recovery_ref("old-unfinished-aaaa", 30)
        self.recovery_ref("young-unfinished-bbbb", 2)
        for index in range(3):
            (self.site / "ctas/data/status.json").write_text(f"dirty {index}")
            self.git("stash", "push", "-q", "-m", f"CTAS generated recovery {index}", "--", "ctas/data")
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "40")
        result = self.maintain()
        self.assertIn("newest 10 commits kept; pruned 1 recovery refs; cleared 3 stash entries", result.stdout)
        self.assertEqual(self.git("rev-parse", "--is-shallow-repository"), "true")
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "10")
        refs = self.git("for-each-ref", "--format=%(refname)", "refs/ctas-recovery/")
        self.assertEqual(refs, "refs/ctas-recovery/young-unfinished-bbbb")
        self.assertEqual(self.git("stash", "list"), "")
        self.assertTrue(self.stamp.read_text().strip().isdigit())

    def test_foreign_stash_entries_are_kept(self):
        (self.site / "ctas/data/status.json").write_text("someone's work")
        self.git("stash", "push", "-q", "-m", "hand-made experiment", "--", "ctas/data")
        (self.site / "ctas/data/status.json").write_text("dirty")
        self.git("stash", "push", "-q", "-m", "CTAS generated recovery x", "--", "ctas/data")
        result = self.maintain()
        self.assertIn("cleared 0 stash entries", result.stdout)
        self.assertEqual(len(self.git("stash", "list").splitlines()), 2)
        self.assertEqual(self.git("stash", "show", "-p", "stash@{1}").count("someone's work"), 1)

    def test_shallow_checkout_still_publishes_and_syncs(self):
        self.maintain()
        (self.site / "ctas/data/status.json").write_text('{"release": "local"}')
        self.git("commit", "-qam", "CTAS data: local release")
        self.git("push", "-q", "origin", "main")
        self.assertEqual(run(["git", "rev-parse", "main"], self.origin).stdout.strip(), self.git("rev-parse", "HEAD"))
        run(["git", "pull", "-q", "--ff-only", "origin", "main"], self.author)
        (self.author / "ctas/data/status.json").write_text('{"release": "remote"}')
        run(["git", "commit", "-qam", "CTAS data: remote release"], self.author)
        run(["git", "push", "-q", "origin", "main"], self.author)
        self.git("fetch", "-q", "origin", "main")
        run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], self.site)
        self.git("merge", "-q", "--ff-only", "origin/main")
        self.assertIn('"remote"', (self.site / "ctas/data/status.json").read_text())

    def test_runs_at_most_once_per_interval(self):
        self.maintain()
        second = self.maintain()
        self.assertEqual(second.stdout, "")
        forced = self.maintain(PUBLISHER_HOUSEKEEPING_EVERY="0", PUBLISHER_HISTORY_DEPTH="5")
        self.assertIn("newest 5 commits kept", forced.stdout)
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "5")

    def test_fetch_failure_skips_trim_without_stamping(self):
        self.git("remote", "set-url", "origin", (Path(self.temporary.name) / "missing.git").as_uri())
        result = self.maintain()
        self.assertIn("history trim skipped", result.stdout)
        self.assertFalse(self.stamp.exists())
        self.assertEqual(self.git("rev-parse", "--is-shallow-repository"), "false")

    def test_disk_floor(self):
        ok = run(["bash", str(SCRIPT), "disk", "0"], self.site, {"PUBLISHER_MIN_FREE_GB": "0"}, check=False)
        self.assertEqual(ok.returncode, 0, ok.stdout)
        full = run(["bash", str(SCRIPT), "disk", str(10**9)], self.site, {"PUBLISHER_MIN_FREE_GB": "999999"}, check=False)
        self.assertEqual(full.returncode, 3)
        self.assertIn("GB free; this run needs 1 GB", full.stdout)
        junk = run(["bash", str(SCRIPT), "disk", "not-a-number"], self.site, {"PUBLISHER_MIN_FREE_GB": "0"}, check=False)
        self.assertEqual(junk.returncode, 0)

    def test_both_runners_call_it_before_publishing(self):
        for runner, namespace, publisher in (("ctas_launchd_runner.sh", "ctas", "publish_ctas.sh"),
                                             ("worldsindex_launchd_runner.sh", "worldsindex", "publish_worldsindex.sh")):
            text = (ROOT / "scripts" / runner).read_text()
            with self.subTest(runner=runner):
                self.assertIn('publisher_housekeeping.sh" disk', text)
                self.assertIn(f'publisher_housekeeping.sh" maintain {namespace} "$BRANCH"', text)
                self.assertLess(text.index("publisher_housekeeping.sh"), text.index(f'scripts/{publisher}"'))
                self.assertLess(text.index("git fetch --quiet origin"), text.index("publisher_housekeeping.sh"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
