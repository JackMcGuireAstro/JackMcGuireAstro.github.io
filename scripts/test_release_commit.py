#!/usr/bin/env python3
"""scripts/release_commit.py: which website commit produced a published release, whether a
commit is on main, and the last successful github-pages deployment (GitHub CLI stubbed)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_commit as rcm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
A, B, C = "a" * 40, "b" * 40, "c" * 40


def gh_stub(answers):
    """A runner that answers `gh api <path>` from a dict of path prefix -> JSON (or an error)."""
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        path = argv[2]
        for prefix, answer in answers.items():
            if path.startswith(prefix):
                if isinstance(answer, Exception):
                    return SimpleNamespace(returncode=1, stdout="", stderr=str(answer))
                return SimpleNamespace(returncode=0, stdout=json.dumps(answer), stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 404")
    runner.calls = calls
    return runner


class ReadTests(unittest.TestCase):
    def test_ctas_prefers_the_certified_head_then_provenance_then_the_marker(self):
        docs = {"certification.json": {"publication_relationship": {"local_head": A}},
                "provenance.json": {"activity": {"ctas:activity/export": {"ctas:site_commit": B}}},
                "cloud-run.json": {"site_commit": C}}
        self.assertEqual(rcm.release_commit("ctas", docs.get)[0], A)
        docs["certification.json"] = {"publication_relationship": {"local_head": "not-a-sha"}}
        self.assertEqual(rcm.release_commit("ctas", docs.get), (B, "provenance.json ctas:site_commit"))
        docs.pop("provenance.json")
        self.assertEqual(rcm.release_commit("ctas", docs.get)[0], C)
        self.assertEqual(rcm.release_commit("ctas", {}.get), (None, "the release records no site commit"))

    def test_worldsindex_reads_the_provenance_then_the_marker_then_the_manifest(self):
        provenance = {"activity": {"wi:activity/static-build/2026-10-23T03:23:49.479Z": {"wi:siteCommit": A},
                                   "wi:activity/atlas-generation/x": {"wi:builderCommit": C}}}
        self.assertEqual(rcm.release_commit("worldsindex", {"provenance.json": provenance, "cloud-run.json": {"site_commit": B}}.get),
                         (A, "provenance.json wi:siteCommit"))
        self.assertEqual(rcm.release_commit("worldsindex", {"cloud-run.json": {"site_commit": B}, "manifest.json": {"siteCommit": C}}.get)[0], B)
        self.assertEqual(rcm.release_commit("worldsindex", {"manifest.json": {"siteCommit": C}}.get)[0], C)
        self.assertIsNone(rcm.release_commit("worldsindex", {"cloud-run.json": {}}.get)[0])

    def test_the_cli_reads_a_data_folder_and_a_data_branch(self):
        with tempfile.TemporaryDirectory(prefix="release-commit-") as folder:
            folder = Path(folder)
            data = folder / "repo/ctas/data"
            data.mkdir(parents=True)
            (data / "certification.json").write_text(json.dumps({"publication_relationship": {"local_head": A}}))
            done = subprocess.run([sys.executable, str(ROOT / "scripts/release_commit.py"), "read", "--product", "ctas",
                                   "--data-dir", str(data)], capture_output=True, text=True)
            self.assertEqual(done.stdout.strip(), A)
            self.assertIn("certification.json", done.stderr)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
            repo = folder / "repo"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "data"], check=True, env=env)
            done = subprocess.run([sys.executable, str(ROOT / "scripts/release_commit.py"), "read", "--product", "ctas",
                                   "--git-dir", str(repo / ".git"), "--ref", "HEAD"], capture_output=True, text=True)
            self.assertEqual(done.stdout.strip(), A)
            nothing = subprocess.run([sys.executable, str(ROOT / "scripts/release_commit.py"), "read", "--product", "worldsindex",
                                      "--git-dir", str(repo / ".git"), "--ref", "HEAD"], capture_output=True, text=True)
            self.assertEqual((nothing.returncode, nothing.stdout), (0, ""))


class GitHubTests(unittest.TestCase):
    def test_on_main_asks_compare(self):
        repo = "o/r"
        self.assertEqual(rcm.on_main(repo, A, A, runner=gh_stub({})), "yes")
        runner = gh_stub({f"repos/{repo}/compare/{B}...{A}": {"status": "ahead"}})
        self.assertEqual(rcm.on_main(repo, B, A, runner=runner), "yes")
        self.assertEqual(runner.calls[0][:3], ["gh", "api", f"repos/{repo}/compare/{B}...{A}"])
        self.assertEqual(rcm.on_main(repo, B, A, runner=gh_stub({"repos/": {"status": "diverged"}})), "no")
        self.assertEqual(rcm.on_main(repo, B, A, runner=gh_stub({"repos/": {"status": "behind"}})), "no")
        self.assertEqual(rcm.on_main(repo, B, A, runner=gh_stub({"repos/": RuntimeError("HTTP 403")})), "unknown")
        self.assertEqual(rcm.on_main(repo, "short", A, runner=gh_stub({})), "unknown")

    def test_last_deployment_is_the_newest_successful_one(self):
        repo = "o/r"
        runner = gh_stub({
            f"repos/{repo}/deployments?environment=github-pages": [{"id": 3, "sha": C}, {"id": 2, "sha": B}, {"id": 1, "sha": A}],
            f"repos/{repo}/deployments/3/statuses": [{"state": "failure"}],
            f"repos/{repo}/deployments/2/statuses": [{"state": "success"}],
            f"repos/{repo}/deployments/1/statuses": [{"state": "success"}],
        })
        self.assertEqual(rcm.last_deployment(repo, runner=runner), B)
        self.assertIsNone(rcm.last_deployment(repo, runner=gh_stub({})))
        self.assertIsNone(rcm.last_deployment(repo, runner=gh_stub({f"repos/{repo}/deployments": []})))


if __name__ == "__main__":
    unittest.main(verbosity=2)
