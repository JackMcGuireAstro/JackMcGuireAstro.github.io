#!/usr/bin/env python3
"""Prove that the dossier chunks of the release being deployed are served by their origin.

Since the delivery batch (2026-10-29) the GitHub Pages site does not carry the CTAS dossier
roots and parts (ctas/data/candidate-chunks/<name>.json, 1.9 GB plain, 281 MB gzipped of a
1 GB allowance). ctas/delivery.json points the browser at the raw.githubusercontent.com
address of the exact ctas-data commit the release came from, pinned by commit (the branch
is replaced wholesale on every release), and ctas/chunk-loader.js verifies every file against
the manifest's byte length and SHA-256 whatever the origin.

This deploy check (integrity class) makes real requests before the pointer is written:

  * the largest chunk root, and one supplemental part when the release has any, are
    fetched from the pinned commit with a browser-like Origin header: HTTP 200,
    Access-Control-Allow-Origin "*", and the body decodes to exactly the bytes and SHA-256
    the manifest declares (and to the file in the tree, so the tree is that commit);
  * a byte-range request answers 206 with a Content-Range header;
  * the manifest itself is readable at the same commit.

Usage: check_chunk_origin.py [--tree .] [--commit SHA] [--repo OWNER/NAME]
The commit defaults to CTAS_DATA_COMMIT, then to refs/remotes/origin/ctas-data of the tree
(what scripts/overlay_published_data.sh archived). Exit 0 when every request passes.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_REPO = "JackMcGuireAstro/JackMcGuireAstro.github.io"
SITE_ORIGIN = "https://jackmcguireastro.github.io"
ATTEMPTS = 3


def request(url: str, headers: dict, timeout: float = 60.0):
    """GET url; returns (status, headers, body) with gzip content-encoding undone."""
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ctas-deploy-check", **headers})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read()
                info = {k.lower(): v for k, v in response.headers.items()}
                if info.get("content-encoding") == "gzip":
                    body = gzip.decompress(body)
                return response.status, info, body
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                return error.code, {k.lower(): v for k, v in error.headers.items()}, b""
            last = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def resolve_commit(tree: Path, explicit: str | None) -> str:
    commit = explicit or os.environ.get("CTAS_DATA_COMMIT") or ""
    if not commit:
        try:
            commit = subprocess.run(["git", "rev-parse", "refs/remotes/origin/ctas-data"], cwd=tree, check=True,
                                    text=True, capture_output=True).stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            commit = ""
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise SystemExit("FAIL  no ctas-data commit to check (set CTAS_DATA_COMMIT or overlay the published data first)")
    return commit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", default=".")
    parser.add_argument("--commit")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO)
    args = parser.parse_args()
    tree = Path(args.tree).resolve()
    manifest_path = tree / "ctas/data/candidate-chunks/manifest.json"
    if not manifest_path.is_file():
        print("FAIL  no ctas/data/candidate-chunks/manifest.json in the tree")
        return 1
    manifest = json.loads(manifest_path.read_text())
    commit = resolve_commit(tree, args.commit)
    base = f"https://raw.githubusercontent.com/{args.repo}/{commit}/"
    chunks = sorted(manifest.get("chunks", []), key=lambda c: int(c["bytes"]), reverse=True)
    parts = manifest.get("parts") or []
    if not chunks:
        print("FAIL  the manifest lists no chunks")
        return 1
    targets = [chunks[0]] + ([parts[0]] if parts else [])
    problems: list[str] = []
    print(f"chunk origin {base} ({len(chunks)} roots, {len(parts)} parts in the manifest)")

    status, headers, body = request(base + "ctas/data/candidate-chunks/manifest.json", {"Origin": SITE_ORIGIN, "Accept-Encoding": "gzip"})
    if status != 200 or hashlib.sha256(body).hexdigest() != hashlib.sha256(manifest_path.read_bytes()).hexdigest():
        problems.append(f"manifest at the origin: HTTP {status}, or not the tree's manifest")
    else:
        print("ok    manifest.json at the origin is the tree's manifest")

    for entry in targets:
        path = entry["path"]
        local = tree / path
        status, headers, body = request(base + path, {"Origin": SITE_ORIGIN, "Accept-Encoding": "gzip"})
        acao = headers.get("access-control-allow-origin", "")
        digest = hashlib.sha256(body).hexdigest()
        if status != 200:
            problems.append(f"{path}: HTTP {status} from the origin")
            continue
        if acao != "*":
            problems.append(f"{path}: Access-Control-Allow-Origin is {acao!r}, not '*' (the browser could not read it)")
        if len(body) != int(entry["bytes"]) or digest != entry["sha256"]:
            problems.append(f"{path}: the origin serves {len(body)} bytes sha256 {digest[:12]}…, the manifest declares {entry['bytes']} / {entry['sha256'][:12]}…")
        elif local.is_file() and hashlib.sha256(local.read_bytes()).hexdigest() != digest:
            problems.append(f"{path}: the origin's file differs from the tree's (the tree is not commit {commit[:10]})")
        else:
            print(f"ok    {path}: {len(body):,} bytes, SHA-256 matches the manifest, CORS '*', content-encoding {headers.get('content-encoding', 'identity')}")
        status, headers, body = request(base + path, {"Origin": SITE_ORIGIN, "Range": "bytes=0-99", "Accept-Encoding": "identity"})
        if status != 206 or "content-range" not in headers:
            problems.append(f"{path}: a byte-range request answered HTTP {status} without Content-Range")
        else:
            print(f"ok    {path}: byte range answered 206 ({headers['content-range']})")

    for problem in problems:
        print("FAIL  " + problem)
    if problems:
        return 1
    print(f"chunk origin verified: raw.githubusercontent.com serves release commit {commit[:10]} with CORS and byte ranges")
    return 0


if __name__ == "__main__":
    sys.exit(main())
