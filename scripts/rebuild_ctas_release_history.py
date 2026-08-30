#!/usr/bin/env python3
"""Rebuild CTAS history from successful public candidate-count transitions.

This is a repair utility for legacy history that was assembled in the working
tree before validation and push succeeded.  It trusts only commits reachable
from the requested public ref and retains one entry per actual public count
transition.  Same-count semantic history resumes under the repaired exporter.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

try:
    from export_ctas_snapshot import (
        catalog_semantic_checksum,
        git_catalog_document,
        semantic_catalog_candidates,
    )
except ModuleNotFoundError:  # imported as scripts.rebuild_ctas_release_history in tests
    from scripts.export_ctas_snapshot import (
        catalog_semantic_checksum,
        git_catalog_document,
        semantic_catalog_candidates,
    )

SCHEMA = "ctas.public-release-history@1.0.0"
SUBJECT = re.compile(r"^CTAS data: (\d+) candidates ")


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.DEVNULL)


def snapshot(repo: Path, commit: str) -> dict:
    document = git_catalog_document(repo, commit)
    if document is None:
        raise ValueError(f"no checksum-valid CTAS catalog at {commit}")
    return document


def semantic_candidates(document: dict) -> list[dict]:
    return semantic_catalog_candidates(document.get("candidates", []))


def semantic_checksum(document: dict) -> str:
    return catalog_semantic_checksum(document.get("candidates", []))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--output", default="ctas/data/release-history.json")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    lines = git(
        repo, "log", "--first-parent", "--format=%H%x09%P%x09%s",
        args.ref, "--", "ctas/data/candidates.json", "ctas/data/catalog-index.json",
        "ctas/data/candidate-chunks/manifest.json",
    ).decode().splitlines()
    releases = []
    for line in lines:
        commit, parents, subject = line.split("\t", 2)
        match = SUBJECT.match(subject)
        if match:
            releases.append((commit, parents.split()[0] if parents else None, int(match.group(1))))

    rebuilt = []
    for index, (commit, parent, count) in enumerate(releases[:-1]):
        previous_count = releases[index + 1][2]
        if count == previous_count:
            continue
        try:
            current_snapshot = snapshot(repo, commit)
            previous_snapshot = snapshot(repo, parent) if parent else {"candidates": []}
        except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
            continue
        current_by_name = {
            str(row["name"]): row for row in semantic_candidates(current_snapshot) if row.get("name")
        }
        previous_by_name = {
            str(row["name"]): row for row in semantic_candidates(previous_snapshot) if row.get("name")
        }
        if len(current_by_name) != count or len(previous_by_name) != previous_count:
            continue
        added = sorted(set(current_by_name) - set(previous_by_name))
        removed = sorted(set(previous_by_name) - set(current_by_name))
        changed = sorted(
            name for name in set(current_by_name) & set(previous_by_name)
            if current_by_name[name] != previous_by_name[name]
        )
        survey_counts = {}
        for name in added:
            survey = str(current_by_name[name].get("discovery_survey") or "source not recorded")
            survey_counts[survey] = survey_counts.get(survey, 0) + 1
        entry = {
            "published_at": current_snapshot.get("generated_at"),
            "change_kind": "candidate-intake",
            "previous_candidate_count": previous_count,
            "candidate_count": count,
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_count": len(changed),
            "added_source_summary": dict(sorted(survey_counts.items())),
            "sample_added": added[:12],
            "sample_removed": removed[:12],
            "summary": (
                f"{len(added):+d} public candidate records and {len(removed)} removals; "
                "source-reported records are not necessarily newly discovered events."
            ),
            "catalog_content_checksum_sha256": semantic_checksum(current_snapshot),
            "previous_catalog_content_checksum_sha256": semantic_checksum(previous_snapshot),
            "base_commit": parent,
            "history_basis": "git-verified-public-candidate-count-transition",
        }
        rebuilt.append(entry)
        if len(rebuilt) >= args.limit:
            break

    if not rebuilt:
        raise SystemExit("no public candidate-count transitions could be reconstructed")
    output = {
        "schema": SCHEMA,
        "generated_at": rebuilt[0]["published_at"],
        "claim_boundary": (
            "Reconstructed entries describe successful public candidate-count transitions, "
            "not independent discoveries or scientific validation. Same-count semantic "
            "history begins with the repaired publisher."
        ),
        "entries": rebuilt,
    }
    destination = repo / args.output
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    temporary.replace(destination)
    print(f"rebuilt {len(rebuilt)} Git-verified public count transitions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
