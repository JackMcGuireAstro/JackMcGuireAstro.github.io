#!/usr/bin/env python3
"""Which website commit produced a published release, and the commits the deploy may fall back to.

The last-good-code fallback (RESILIENCE.md) uses "the code that produced the currently
published release" as the known-good code. It is read back from the release itself:

* CTAS: the certificate's ``publication_relationship.local_head`` (the HEAD whose code
  bytes the certificate binds), else the exporter's ``ctas:site_commit`` in provenance.json,
  else ``site_commit`` in the cloud-run marker (cloud-run.json);
* WorldsIndex: the static build's ``wi:siteCommit`` in provenance.json (a manifest artifact,
  written by the code that built the release), else ``site_commit`` in the cloud-run marker,
  else ``siteCommit`` in the manifest.

A commit is only used when it is an ancestor of main (or main itself): a release built
from another branch by a manual run must never become the fallback code. That is asked of
the GitHub API (``compare``), because the Actions checkouts are shallow.

    release_commit.py read --product ctas|worldsindex (--data-dir DIR | --git-dir G --ref REF)
    release_commit.py on-main --repo OWNER/NAME --commit SHA --main SHA      prints yes | no | unknown
    release_commit.py last-deployment --repo OWNER/NAME [--environment github-pages]

``read`` prints the commit (or nothing); the source is on stderr. ``last-deployment``
prints the commit of the newest github-pages deployment whose latest status is success
(GH_TOKEN, deployments: read). Nothing is ever printed but commits and plain words.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

SHA = re.compile(r"^[0-9a-f]{40}$")


def valid(value) -> str | None:
    return value if isinstance(value, str) and SHA.fullmatch(value) else None


def ctas_commit(read: Callable[[str], dict | None]) -> tuple[str | None, str]:
    certificate = read("certification.json") or {}
    commit = valid((certificate.get("publication_relationship") or {}).get("local_head"))
    if commit:
        return commit, "certification.json publication_relationship.local_head"
    provenance = read("provenance.json") or {}
    for activity in (provenance.get("activity") or {}).values():
        commit = valid((activity or {}).get("ctas:site_commit")) if isinstance(activity, dict) else None
        if commit:
            return commit, "provenance.json ctas:site_commit"
    commit = valid((read("cloud-run.json") or {}).get("site_commit"))
    if commit:
        return commit, "cloud-run.json site_commit"
    return None, "the release records no site commit"


def worldsindex_commit(read: Callable[[str], dict | None]) -> tuple[str | None, str]:
    provenance = read("provenance.json") or {}
    for key, activity in sorted((provenance.get("activity") or {}).items(), reverse=True):
        if key.startswith("wi:activity/static-build") and isinstance(activity, dict) and valid(activity.get("wi:siteCommit")):
            return activity["wi:siteCommit"], "provenance.json wi:siteCommit"
    commit = valid((read("cloud-run.json") or {}).get("site_commit"))
    if commit:
        return commit, "cloud-run.json site_commit"
    commit = valid((read("manifest.json") or {}).get("siteCommit"))
    if commit:
        return commit, "manifest.json siteCommit"
    return None, "the release records no site commit"


def reader_for_dir(data_dir: str | Path) -> Callable[[str], dict | None]:
    def read(name: str) -> dict | None:
        try:
            return json.loads((Path(data_dir) / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return read


def reader_for_git(git_dir: str, ref: str, prefix: str) -> Callable[[str], dict | None]:
    def read(name: str) -> dict | None:
        completed = subprocess.run(["git", f"--git-dir={git_dir}", "show", f"{ref}:{prefix}/{name}"],
                                   capture_output=True, text=True)
        if completed.returncode != 0:
            return None
        try:
            return json.loads(completed.stdout)
        except ValueError:
            return None
    return read


def release_commit(product: str, read: Callable[[str], dict | None]) -> tuple[str | None, str]:
    return ctas_commit(read) if product == "ctas" else worldsindex_commit(read)


def gh_json(args: list[str], runner: Callable = subprocess.run):
    completed = runner(["gh", "api", *args], capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "").strip().splitlines()[-1:] or ["gh api failed"])
    return json.loads(completed.stdout or "null")


def on_main(repo: str, commit: str, main: str, runner: Callable = subprocess.run) -> str:
    """yes when ``commit`` is ``main`` or one of its ancestors, no when it is not, unknown on error."""
    if not (valid(commit) and valid(main)):
        return "unknown"
    if commit == main:
        return "yes"
    try:
        status = (gh_json([f"repos/{repo}/compare/{commit}...{main}", "--jq", "{status: .status}"], runner) or {}).get("status")
    except (RuntimeError, ValueError, OSError):
        return "unknown"
    return "yes" if status in ("ahead", "identical") else "no" if status in ("behind", "diverged") else "unknown"


def last_deployment(repo: str, environment: str = "github-pages", runner: Callable = subprocess.run,
                    limit: int = 10) -> str | None:
    """Commit of the newest deployment to ``environment`` whose latest status is success."""
    try:
        deployments = gh_json([f"repos/{repo}/deployments?environment={environment}&per_page={limit}"], runner) or []
    except (RuntimeError, ValueError, OSError):
        return None
    for deployment in deployments:
        try:
            statuses = gh_json([f"repos/{repo}/deployments/{deployment['id']}/statuses?per_page=1"], runner) or []
        except (RuntimeError, ValueError, OSError, KeyError, TypeError):
            continue
        if statuses and statuses[0].get("state") == "success" and valid(deployment.get("sha")):
            return deployment["sha"]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("read")
    p.add_argument("--product", required=True, choices=["ctas", "worldsindex"])
    p.add_argument("--data-dir")
    p.add_argument("--git-dir")
    p.add_argument("--ref")
    p = sub.add_parser("on-main")
    p.add_argument("--repo", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--main", required=True)
    p = sub.add_parser("last-deployment")
    p.add_argument("--repo", required=True)
    p.add_argument("--environment", default="github-pages")
    args = parser.parse_args(argv)
    if args.command == "read":
        prefix = "ctas/data" if args.product == "ctas" else "worldsindex/data"
        if args.data_dir:
            read = reader_for_dir(args.data_dir)
        elif args.git_dir and args.ref:
            read = reader_for_git(args.git_dir, args.ref, prefix)
        else:
            parser.error("read needs --data-dir, or --git-dir and --ref")
        commit, source = release_commit(args.product, read)
        print(f"release commit: {commit or 'none'} ({source})", file=sys.stderr)
        if commit:
            print(commit)
        return 0
    if args.command == "on-main":
        print(on_main(args.repo, args.commit, args.main))
        return 0
    commit = last_deployment(args.repo, args.environment)
    if commit:
        print(commit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
