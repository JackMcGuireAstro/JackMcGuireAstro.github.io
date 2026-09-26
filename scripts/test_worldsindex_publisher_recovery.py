#!/usr/bin/env python3
"""Exercise the WorldsIndex runner's preserve-and-clear step and the publisher's
discard against isolated Git repositories, mirroring test_ctas_publisher_recovery.py.

The WorldsIndex publisher never hit the failure that stalled CTAS in September 2026
(its releases are small), but it carried the same pathspec `git stash` and the same
list-based restore. These tests pin the ported behaviour so the two runners cannot
drift apart again."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GitFixture(unittest.TestCase):
    prefix = "worldsindex-fixture-"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=self.prefix)
        self.addCleanup(self.temporary.cleanup)
        self.site = Path(self.temporary.name).resolve()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "WorldsIndex fixture")
        self.git("config", "user.email", "worldsindex@example.invalid")
        self.write("worldsindex/assets/app.js", "public code")
        self.write("worldsindex/data/manifest.json", '{"generatedAt": "published"}')
        self.write("worldsindex/data/source-monitor.json", "published monitor")
        self.write("worldsindex/data/details/00.json.gz", "published shard")
        self.write("worldsindex/data/details/01.json.gz", "shard to be retired")
        self.git("add", "--all")
        self.git("commit", "-qm", "WorldsIndex data: fixture")
        self.head = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", self.head)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.site, check=True,
                              text=True, capture_output=True).stdout.strip()

    def write(self, path, content):
        target = self.site / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def dirty_build(self):
        self.write("worldsindex/data/manifest.json", '{"generatedAt": "unfinished"}')
        self.write("worldsindex/data/source-monitor.json", "unfinished monitor")
        self.write("worldsindex/data/details/ff.json.gz", "new shard")
        self.write("worldsindex/data/registry.json.gz", "new artifact")
        (self.site / "worldsindex/data/details/01.json.gz").unlink()


class RunnerPreserveTests(GitFixture):
    prefix = "worldsindex-runner-"

    def setUp(self):
        super().setUp()
        self.runner = (ROOT / "scripts/worldsindex_launchd_runner.sh").read_text()
        self.block = self.runner[self.runner.index("# Only generated public data may be dirty"):
                                 self.runner.index("export GIT_TERMINAL_PROMPT=0")]

    def run_block(self):
        script = ("set -uo pipefail\nBRANCH=main\n"
                  "say() { printf '%s\\n' \"$*\"; }\n"
                  "die() { printf 'FAIL  %s\\n' \"$*\" >&2; exit 1; }\n" + self.block)
        return subprocess.run(["/bin/bash", "-c", script], cwd=self.site, text=True, capture_output=True)

    def recovery_refs(self):
        refs = self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/worldsindex-recovery/")
        return [line.split() for line in refs.splitlines()]

    def test_unfinished_data_is_preserved_on_a_recovery_ref_and_cleared(self):
        self.dirty_build()
        result = self.run_block()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("preserved unfinished generated data at refs/worldsindex-recovery/", result.stdout)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        refs = self.recovery_refs()
        self.assertEqual(len(refs), 1)
        ref, commit = refs[0]
        self.assertIn("-unfinished-", ref)
        self.assertEqual(self.git("show", "-s", "--format=%P", commit), self.head)
        self.assertEqual(self.git("show", commit + ":worldsindex/data/manifest.json"), '{"generatedAt": "unfinished"}')
        self.assertEqual(self.git("show", commit + ":worldsindex/data/details/ff.json.gz"), "new shard")
        self.assertEqual(self.git("show", commit + ":worldsindex/data/registry.json.gz"), "new artifact")
        self.assertEqual(self.git("show", commit + ":worldsindex/assets/app.js"), "public code")
        self.assertNotIn("worldsindex/data/details/01.json.gz", self.git("ls-tree", "-r", "--name-only", commit))
        self.assertEqual((self.site / "worldsindex/data/details/01.json.gz").read_text(), "shard to be retired")

    def test_preservation_failure_still_clears_the_checkout(self):
        self.dirty_build()
        (self.site / ".git/refs/worldsindex-recovery").write_text("blocked")
        result = self.run_block()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not preserve unfinished generated data; discarding it", result.stdout)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual((self.site / "worldsindex/data/manifest.json").read_text(), '{"generatedAt": "published"}')

    def test_clean_checkout_is_left_alone(self):
        result = self.run_block()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.recovery_refs(), [])

    def test_non_data_changes_still_stop_the_runner(self):
        self.write("worldsindex/assets/app.js", "edited code")
        self.write("worldsindex/data/manifest.json", "unfinished")
        result = self.run_block()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected non-data changes", result.stderr)
        self.assertEqual((self.site / "worldsindex/assets/app.js").read_text(), "edited code")
        self.assertEqual((self.site / "worldsindex/data/manifest.json").read_text(), "unfinished")
        self.assertEqual(self.recovery_refs(), [])

    def test_generated_data_never_goes_through_a_patch(self):
        code = "\n".join(line for line in self.runner.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("git stash", code)
        self.assertNotIn("git apply", code)


class PublisherDiscardTests(GitFixture):
    prefix = "worldsindex-discard-"

    def setUp(self):
        super().setUp()
        publisher = (ROOT / "scripts/publish_worldsindex.sh").read_text()
        self.functions = publisher[publisher.index("discard_generated_files() {"):
                                   publisher.index("trap cleanup EXIT")]
        self.write("worldsindex/assets/app.js", "edited code stays")
        self.dirty_build()
        self.git("add", "--", "worldsindex/data/manifest.json")

    def run_script(self, body):
        script = ("set -uo pipefail\nSITE=" + repr(str(self.site)) + "\n"
                  "LOCKDIR=" + repr(str(self.site / "lock.d")) + "\n" + self.functions + "\n" + body)
        return subprocess.run(["/bin/bash", "-c", script], cwd=self.site, text=True, capture_output=True)

    def assert_generated_files_match_head(self):
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all", "--", "worldsindex/data"), "")
        self.assertEqual((self.site / "worldsindex/data/manifest.json").read_text(), '{"generatedAt": "published"}')
        self.assertEqual((self.site / "worldsindex/data/details/01.json.gz").read_text(), "shard to be retired")
        self.assertFalse((self.site / "worldsindex/data/details/ff.json.gz").exists())
        self.assertFalse((self.site / "worldsindex/data/registry.json.gz").exists())
        self.assertEqual((self.site / "worldsindex/assets/app.js").read_text(), "edited code stays")

    def test_discard_returns_all_generated_files_to_head_and_keeps_code_edits(self):
        result = self.run_script("discard_generated_files\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_generated_files_match_head()

    def test_failed_run_cleanup_discards_new_artifacts(self):
        (self.site / "lock.d").mkdir()
        result = self.run_script("false\ncleanup\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assert_generated_files_match_head()
        self.assertFalse((self.site / "lock.d").exists())

    def test_successful_run_cleanup_keeps_the_working_tree(self):
        (self.site / "lock.d").mkdir()
        result = self.run_script("true\ncleanup\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.site / "worldsindex/data/manifest.json").read_text(), '{"generatedAt": "unfinished"}')
        self.assertTrue((self.site / "worldsindex/data/details/ff.json.gz").exists())

    def test_non_committing_exits_discard_generated_files(self):
        publisher = (ROOT / "scripts/publish_worldsindex.sh").read_text()
        for message in ("--dry-run:",):
            with self.subTest(message=message):
                index = publisher.index(message)
                following = publisher[index:publisher.index("exit 0", index)]
                self.assertIn("discard_generated_files", following)
        self.assertNotIn('restore --source=HEAD --worktree -- "${PUBLIC_FILES[@]}"', publisher)
        self.assertNotIn('restore --source=HEAD --staged --worktree -- "${PUBLIC_FILES[@]}"', publisher)


if __name__ == "__main__":
    unittest.main(verbosity=2)
