#!/usr/bin/env python3
"""Exercise scripts/data_branch.sh against a file:// origin and a runtime checkout."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/data_branch.sh"
DISPATCHER = ".github/workflows/data-branch-deploy.yml"
ENV = dict(os.environ, GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="maintenance.auto", GIT_CONFIG_VALUE_0="false",
           GIT_CONFIG_KEY_1="gc.auto", GIT_CONFIG_VALUE_1="0", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
for _key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
    ENV.pop(_key, None)


def run(args, cwd, check=True, **extra):
    result = subprocess.run(args, cwd=cwd, env=dict(ENV, **extra), text=True, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError(f"{args} failed ({result.returncode}): {result.stdout}{result.stderr}")
    return result


class DataBranchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="data-branch-")
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name).resolve()
        self.origin = base / "origin.git"
        run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], base)
        author = base / "author"
        run(["git", "init", "-q", "-b", "main", str(author)], base)
        for key, value in (("user.name", "fixture"), ("user.email", "fixture@example.invalid")):
            run(["git", "config", key, value], author)
        (author / ".github/workflows").mkdir(parents=True)
        (author / DISPATCHER).write_text("name: Deploy a new data release\n")
        (author / ".gitignore").write_text("/ctas/data/*\n!/ctas/data/observatories.json\n")
        (author / "ctas/data").mkdir(parents=True)
        (author / "ctas/data/observatories.json").write_text("{}")
        (author / "ctas/app.js").write_text("code")
        run(["git", "add", "--all"], author)
        run(["git", "commit", "-qm", "code"], author)
        run(["git", "remote", "add", "origin", self.origin.as_uri()], author)
        run(["git", "push", "-q", "origin", "main"], author)
        self.site = base / "site"
        run(["git", "clone", "-q", self.origin.as_uri(), str(self.site)], base)
        self.store = self.site / ".git/ctas-data-store"
        self.list = base / "release-files.txt"

    def write_release(self, version, parts=2):
        data = self.site / "ctas/data"
        (data / "candidate-chunks").mkdir(parents=True, exist_ok=True)
        (data / "status.json").write_text(f'{{"release": {version}}}')
        (data / "candidate-chunks/000.json").write_text(f'{{"root": {version}, "pad": "{"x" * 5000}"}}')
        paths = ["ctas/data/status.json", "ctas/data/candidate-chunks/000.json"]
        for index in range(1, parts + 1):
            name = f"ctas/data/candidate-chunks/000.part-{index:06d}.json"
            (self.site / name).write_text(f'{{"part": {index}, "release": {version}}}')
            paths.append(name)
        self.list.write_text("\n".join(paths) + "\n")
        return paths

    def helper(self, *args):
        return run(["bash", str(SCRIPT), *args], self.site, check=False)

    def publish(self, message="CTAS data: fixture"):
        return self.helper("publish", str(self.store), "ctas-data", message, str(self.list))

    def origin_git(self, *args):
        return run(["git", *args], self.origin).stdout.strip()

    def test_identity_comes_from_the_checkout_or_a_neutral_fallback(self):
        self.write_release(1)
        self.assertEqual(self.publish().returncode, 0, "publishing must not need a global git identity")
        self.assertEqual(self.origin_git("log", "-1", "--format=%an <%ae>", "ctas-data"), "Site data publisher <publisher@localhost>")
        run(["git", "config", "user.name", "Jack"], self.site)
        run(["git", "config", "user.email", "jack@example.invalid"], self.site)
        self.write_release(2)
        self.assertEqual(self.publish().returncode, 0)
        self.assertEqual(self.origin_git("log", "-1", "--format=%an <%ae> / %cn", "ctas-data"), "Jack <jack@example.invalid> / Jack")

    def test_sync_reports_a_missing_branch(self):
        result = self.helper("sync", str(self.store), "ctas-data")
        self.assertEqual(result.returncode, 4)
        self.assertIn("does not exist", result.stdout)

    def test_publish_creates_a_single_parentless_commit_with_the_dispatcher(self):
        paths = self.write_release(1)
        result = self.publish("CTAS data: 1 candidate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("published "))
        tip = self.origin_git("rev-parse", "ctas-data")
        self.assertEqual(result.stdout.split()[1], tip)
        self.assertEqual(self.origin_git("log", "--format=%s", "ctas-data"), "CTAS data: 1 candidate")
        self.assertEqual(self.origin_git("rev-list", "--count", "ctas-data"), "1")
        files = set(self.origin_git("ls-tree", "-r", "--name-only", "ctas-data").splitlines())
        self.assertEqual(files, set(paths) | {DISPATCHER})
        self.assertNotIn("ctas/data/observatories.json", files)
        # main is untouched and the runtime checkout stays clean
        self.assertEqual(self.origin_git("rev-list", "--count", "main"), "1")
        self.assertEqual(run(["git", "status", "--porcelain"], self.site).stdout, "")

    def test_unchanged_release_is_not_republished(self):
        self.write_release(1)
        first = self.publish()
        self.assertEqual(first.returncode, 0)
        second = self.publish()
        self.assertEqual(second.returncode, 10, second.stdout)
        self.assertEqual(second.stdout.split(), ["unchanged", first.stdout.split()[1]])

    def test_a_release_that_only_adds_a_file_is_published(self):
        self.write_release(1)
        self.assertEqual(self.publish().returncode, 0)
        page = self.site / "ctas/data/catalog-pages/0001.json"
        page.parent.mkdir(parents=True)
        page.write_text('{"candidate_rows": []}')
        with self.list.open("a") as handle:
            handle.write("ctas/data/catalog-pages/0001.json\n")
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("ctas/data/catalog-pages/0001.json", self.origin_git("ls-tree", "-r", "--name-only", "ctas-data"))

    def test_each_release_replaces_the_previous_one_and_the_store_stays_small(self):
        self.write_release(1, parts=3)
        self.assertEqual(self.publish().returncode, 0)
        sync = self.helper("sync", str(self.store), "ctas-data")
        self.assertEqual(sync.returncode, 0)
        for version in range(2, 6):
            (self.site / "ctas/data/candidate-chunks/000.part-000003.json").unlink(missing_ok=True)
            self.write_release(version, parts=2)
            result = self.publish(f"CTAS data: release {version}")
            self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.origin_git("rev-list", "--count", "ctas-data"), "1")
        self.assertEqual(self.origin_git("log", "--format=%s", "ctas-data"), "CTAS data: release 5")
        self.assertNotIn("000.part-000003.json", self.origin_git("ls-tree", "-r", "--name-only", "ctas-data"))
        self.assertIn('"release": 5', self.origin_git("show", "ctas-data:ctas/data/status.json"))
        reachable = run(["git", "--git-dir", str(self.store), "rev-list", "--objects", "--all"], self.site).stdout.splitlines()
        stored = run(["git", "--git-dir", str(self.store), "count-objects", "-v"], self.site).stdout
        in_pack = int(next(line.split()[1] for line in stored.splitlines() if line.startswith("in-pack")))
        loose = int(next(line.split()[1] for line in stored.splitlines() if line.startswith("count")))
        self.assertLessEqual(in_pack + loose, len(reachable))

    def test_failed_push_is_kept_and_retried(self):
        self.write_release(1)
        self.assertEqual(self.publish().returncode, 0)
        self.write_release(2)
        run(["git", "remote", "set-url", "origin", (Path(self.temporary.name) / "offline.git").as_uri()], self.site)
        failed = self.publish("CTAS data: release 2")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("kept as refs/pending/ctas-data", failed.stdout)
        still_failing = self.helper("retry", str(self.store), "ctas-data")
        self.assertEqual(still_failing.returncode, 1)
        run(["git", "remote", "set-url", "origin", self.origin.as_uri()], self.site)
        retried = self.helper("retry", str(self.store), "ctas-data")
        self.assertEqual(retried.returncode, 0, retried.stdout)
        self.assertIn("retained from a failed push", retried.stdout)
        self.assertEqual(self.origin_git("log", "--format=%s", "ctas-data"), "CTAS data: release 2")
        self.assertEqual(self.helper("retry", str(self.store), "ctas-data").stdout, "")

    def test_missing_listed_file_publishes_nothing(self):
        self.write_release(1)
        with self.list.open("a") as handle:
            handle.write("ctas/data/candidate-chunks/fff.json\n")
        result = self.publish()
        self.assertEqual(result.returncode, 1)
        self.assertIn("listed files are missing", result.stdout)
        self.assertEqual(run(["git", "ls-remote", "--heads", self.origin.as_uri(), "ctas-data"], self.site).stdout, "")

    def test_overlay_places_the_release_in_ignored_paths(self):
        self.write_release(7)
        self.assertEqual(self.publish().returncode, 0)
        shutil.rmtree(self.site / "ctas/data/candidate-chunks")
        (self.site / "ctas/data/status.json").unlink()
        (self.site / "ctas/data/stale-leftover.json").write_text("old")
        overlay = run(["bash", str(ROOT / "scripts/overlay_published_data.sh"), "ctas-data"], self.site)
        self.assertTrue(overlay.stdout.startswith("ctas-data "))
        self.assertIn('"release": 7', (self.site / "ctas/data/status.json").read_text())
        self.assertTrue((self.site / "ctas/data/candidate-chunks/000.part-000002.json").exists())
        self.assertFalse((self.site / "ctas/data/stale-leftover.json").exists())
        self.assertTrue((self.site / "ctas/data/observatories.json").exists())
        self.assertEqual(run(["git", "status", "--porcelain"], self.site).stdout, "")

    def test_publishers_never_push_main(self):
        for name in ("publish_ctas.sh", "publish_worldsindex.sh"):
            text = (ROOT / "scripts" / name).read_text()
            with self.subTest(publisher=name):
                self.assertNotIn("git push", text)
                self.assertNotIn("git commit", text)
                self.assertIn("scripts/data_branch.sh publish", text)
        helper = SCRIPT.read_text()
        self.assertIn('"+$commit:refs/heads/$BRANCH"', helper)
        self.assertNotIn("refs/heads/main", helper)


class CompressionTests(unittest.TestCase):
    def test_chunks_are_gzipped_and_the_manifest_is_not(self):
        import gzip
        import hashlib
        import json
        with tempfile.TemporaryDirectory(prefix="ctas-compress-") as folder:
            chunks = Path(folder) / "ctas/data/candidate-chunks"
            chunks.mkdir(parents=True)
            (chunks / "manifest.json").write_text("{}")
            originals = {}
            for name in ("000.json", "001.json", "000.part-000001.json"):
                raw = (name * 400).encode()
                (chunks / name).write_bytes(raw)
                originals[name] = hashlib.sha256(raw).hexdigest()
            result = run(["bash", str(ROOT / "scripts/compress_ctas_chunks.sh"), folder], folder)
            self.assertIn("compressed 3 CTAS chunk files", result.stdout)
            self.assertTrue((chunks / "manifest.json").exists())
            delivery = json.loads((Path(folder) / "ctas/delivery.json").read_text())
            self.assertEqual(delivery["candidate_chunk_encoding"], "gzip")
            for name, digest in originals.items():
                self.assertFalse((chunks / name).exists())
                self.assertEqual(hashlib.sha256(gzip.decompress((chunks / (name + ".gz")).read_bytes())).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
