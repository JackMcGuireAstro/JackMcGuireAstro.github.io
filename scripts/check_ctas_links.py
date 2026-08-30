#!/usr/bin/env python3
"""Audit every public CTAS URL and cautiously sample one live TNS object page.

The structural audit is exhaustive across the candidate catalog and source
universe. The live check is deliberately narrower: it samples one canonical
TNS object page and must never be described as health coverage for every
linked provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

UTC = timezone.utc
SCHEMA = "ctas.link-health@1.0.0"
AUDIT_VERSION = "2.0.0"
TNS_DESIGNATION = re.compile(r"^(?:AT|SN)?(\d{4}[a-z]+)$", re.IGNORECASE)
TNS_OBJECT_PATH = re.compile(r"^/object/(\d{4}[a-z]+)$", re.IGNORECASE)
FILE_ARTIFACT = re.compile(
    r"\.(?:ascii?|csv|ecsv|fits?|fits\.gz|json|pdf|tar|tgz|txt|vot|xml|zip)$",
    re.IGNORECASE,
)
ALLOWED_HOSTS = {
    "api.fink-portal.org", "api.ztf.fink-portal.org", "apps.aavso.org",
    "archive.eso.org", "archive.stsci.edu", "asas-sn.osu.edu", "blackgem.org",
    "cgbm.calet.jp", "chime-experiment.ca", "doc.lsst.fink-broker.org",
    "docs.aavso.org", "ep.bao.ac.cn", "fallingstar-data.com", "fink-portal.org",
    "gcn.gsfc.nasa.gov", "gcn.nasa.gov", "github.com", "goto-observatory.org",
    "heasarc.gsfc.nasa.gov", "irsa.ipac.caltech.edu", "lasair.readthedocs.io",
    "lasair-ztf.lsst.ac.uk",
    "mast.stsci.edu", "maxi.riken.jp", "ned.ipac.caltech.edu",
    "observ.pereplet.ru", "outerspace.stsci.edu", "roc-2.icecube.wisc.edu",
    "roc.icecube.wisc.edu", "rubinobservatory.org", "simbad.cds.unistra.fr",
    "ui.adsabs.harvard.edu", "vizier.cds.unistra.fr", "wfst.bao.ac.cn",
    "www.aavso.org", "www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca",
    "www.cosmos.esa.int", "www.ivoa.net", "www.wis-tns.org",
    "www.wiserep.org", "yse.ucsc.edu", "archive.gemini.edu",
    "tom-toolkit.readthedocs.io", "ampelproject.github.io",
    "antares.noirlab.edu", "babamul.caltech.edu",
    "pitt-broker.readthedocs.io", "ztf.uw.edu",
}

ROLE_DEFINITIONS = {
    "exact-object": "A provider URL naming a particular astronomical object.",
    "exact-record": "A provider URL naming a particular notice, circular, catalog row, or spectrum record.",
    "artifact": "A direct public data or document artifact URL.",
    "query": "A reproducible provider query; rerunning it may return current rather than frozen results.",
    "documentation": "Provider documentation or source-description material.",
    "generic-reference": "A provider landing page, API root, notice index, or other non-object-specific reference.",
}


def stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def safe_parse(url: str):
    """Return a parsed public URL or None for syntactically unusable input."""
    try:
        parsed = urlparse(url)
        # Accessing hostname and port performs additional validation.
        _ = parsed.hostname
        _ = parsed.port
    except (TypeError, ValueError):
        return None
    return parsed


def json_path(path: tuple[object, ...]) -> str:
    return ".".join("[]" if isinstance(part, int) else str(part) for part in path)


def iter_urls(
    value: object,
    *,
    artifact: str,
    path: tuple[object, ...] = (),
    candidate_name: str | None = None,
    source_key: str | None = None,
    designation: str | None = None,
) -> Iterator[dict]:
    """Yield every nested HTTP(S) occurrence with its nearest useful context."""
    if isinstance(value, dict):
        local_candidate = candidate_name
        if len(path) >= 2 and path[0] == "candidates":
            local_candidate = str(value.get("name") or candidate_name or "") or None
        local_source = str(
            value.get("source_key") or value.get("source_id") or
            value.get("provider") or source_key or ""
        ) or None
        # A nested related-object record may intentionally name a different
        # TNS object than the enclosing candidate.  Preserve that nearest
        # provider identifier so the URL is checked against the related
        # object itself, rather than incorrectly against the parent event.
        local_designation = str(
            value.get("designation") or value.get("external_id") or designation or ""
        ) or None
        for key, child in value.items():
            yield from iter_urls(
                child,
                artifact=artifact,
                path=path + (key,),
                candidate_name=local_candidate,
                source_key=local_source,
                designation=local_designation,
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_urls(
                child,
                artifact=artifact,
                path=path + (index,),
                candidate_name=candidate_name,
                source_key=source_key,
                designation=designation,
            )
    elif isinstance(value, str) and value.lower().startswith(("http://", "https://")):
        yield {
            "artifact": artifact,
            "json_path": json_path(path),
            "field": str(path[-1]) if path else "",
            "candidate_name": candidate_name,
            "source_key": source_key,
            "designation": designation,
            "url": value,
        }


def renderability(url: str) -> tuple[str, str | None]:
    """Classify a link for the public UI without treating suppression as failure."""
    parsed = safe_parse(url)
    if parsed is None or not parsed.scheme or not parsed.hostname:
        return "suppressed-malformed", "URL could not be parsed safely"
    if parsed.scheme.lower() != "https":
        return "suppressed-insecure", "only HTTPS links are rendered publicly"
    if parsed.username or parsed.password or parsed.port:
        return "suppressed-malformed", "credentials and explicit ports are not rendered publicly"
    if parsed.hostname.lower() not in ALLOWED_HOSTS:
        return "suppressed-unallowlisted", "origin is not in the public render allowlist"
    if parsed.hostname.lower() == "www.wis-tns.org" and parsed.path.startswith("/object"):
        if parsed.query or parsed.fragment or not TNS_OBJECT_PATH.fullmatch(parsed.path):
            return "suppressed-malformed", "TNS object URL is not an exact canonical public object path"
    return "https-allowlisted-renderable", None


def link_role(occurrence: dict) -> str:
    """Assign the narrowest defensible role supported by URL structure."""
    url = occurrence["url"]
    parsed = safe_parse(url)
    if parsed is None:
        return "generic-reference"
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path or "")
    field = occurrence.get("field", "").lower()

    if (
        "documentation" in field or host.endswith("readthedocs.io") or
        host.startswith("doc.") or host.startswith("docs.")
    ):
        return "documentation"
    if (
        field in {"public_download_url", "download_url", "artifact_url", "skymap_url"} or
        FILE_ARTIFACT.search(path)
    ):
        return "artifact"

    if host == "www.wis-tns.org" and TNS_OBJECT_PATH.fullmatch(path):
        return "exact-object"
    if host == "fink-portal.org" and re.fullmatch(r"/ZTF\d{2}[a-z]+/?", path, re.IGNORECASE):
        return "exact-object"
    if host == "lasair-ztf.lsst.ac.uk" and re.fullmatch(r"/object/ZTF\d{2}[a-z]+/?", path, re.IGNORECASE):
        return "exact-object"
    if host == "simbad.cds.unistra.fr" and path.startswith("/simbad/sim-id") and parsed.query:
        return "exact-object"

    if host in {"gcn.nasa.gov", "gcn.gsfc.nasa.gov"}:
        if re.fullmatch(r"/circulars/\d+/?", path) or re.search(r"/[^/]+\.(?:amon|txt|xml)$", path, re.IGNORECASE):
            return "exact-record"
        if path.startswith("/other/") and path.rstrip("/").count("/") >= 2:
            return "exact-record"
    if host == "www.wiserep.org" and "/form-edit/spectrum/" in path:
        return "exact-record"
    if host == "ui.adsabs.harvard.edu" and re.fullmatch(r"/abs/[^/]+/?", path):
        return "exact-record"
    if host == "vizier.cds.unistra.fr" and "VizieR-5" in path and parsed.query:
        return "exact-record"

    # A field called query_evidence_url is not automatically evidence that a
    # query was encoded. API roots and provider indexes remain generic links.
    if path in {"", "/"} and not parsed.query:
        return "generic-reference"
    if host in {"gcn.nasa.gov", "gcn.gsfc.nasa.gov"} and path.rstrip("/") in {
        "/notices", "/missions", "/circulars",
    } and not parsed.query:
        return "generic-reference"

    query_markers = (
        "query" in field or field == "query_evidence_url" or bool(parsed.query) or
        "nph-query" in path.lower() or "search" in path.lower()
    )
    if query_markers:
        return "query"
    return "generic-reference"


def is_declared_tns_object(occurrence: dict) -> bool:
    parsed = safe_parse(occurrence["url"])
    host = (parsed.hostname or "").lower() if parsed else ""
    path = parsed.path if parsed else ""
    field = occurrence.get("field", "").lower()
    return bool(
        (host == "www.wis-tns.org" and path.startswith("/object")) or
        (field == "object_specific_result_url" and occurrence.get("source_key") == "tns") or
        (occurrence.get("source_key") == "tns" and field in {"url", "source_url"})
    )


def validate_tns_object(occurrence: dict) -> tuple[dict | None, str | None]:
    """Strictly bind each declared TNS object link to one canonical object id."""
    url = occurrence["url"]
    parsed = safe_parse(url)
    if parsed is None:
        return None, "malformed TNS object URL"
    match = TNS_OBJECT_PATH.fullmatch(parsed.path)
    if (
        parsed.scheme != "https" or parsed.hostname != "www.wis-tns.org" or
        parsed.username or parsed.password or parsed.port or parsed.query or
        parsed.fragment or not match
    ):
        return None, "malformed TNS object link"
    object_id = match.group(1).lower()
    declared_designation = occurrence.get("designation")
    if declared_designation:
        designation_match = TNS_DESIGNATION.fullmatch(str(declared_designation))
        if not designation_match or designation_match.group(1).lower() != object_id:
            return None, "TNS object link does not match its declared designation"
    candidate_name = occurrence.get("candidate_name")
    # An explicit nearest-record designation is more specific than the
    # enclosing candidate name (for example a publication's related_objects
    # list).  Only fall back to the candidate identity when no such binding is
    # retained on the URL-bearing record.
    if candidate_name and not declared_designation:
        candidate_match = TNS_DESIGNATION.fullmatch(str(candidate_name))
        # A candidate can legitimately use a non-TNS preferred name, so only
        # enforce identity when that name itself is a canonical TNS designation.
        if candidate_match and candidate_match.group(1).lower() != object_id:
            return None, "TNS object link does not match the candidate designation"
    return {
        "candidate_name": candidate_name,
        "object_id": object_id,
        "url": url,
        "json_path": occurrence.get("json_path"),
    }, None


def audit_links(snapshot: dict, universe: dict | None) -> tuple[dict, list[dict], list[str]]:
    occurrences = list(iter_urls(snapshot, artifact="candidate-catalog"))
    if universe is not None:
        occurrences.extend(iter_urls(universe, artifact="source-universe"))

    render_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    artifact_counts: dict[str, Counter[str]] = {}
    host_counts: Counter[str] = Counter()
    suppressed_samples: list[dict] = []
    structural_problems: list[str] = []
    tns_links: list[dict] = []

    for occurrence in occurrences:
        state, reason = renderability(occurrence["url"])
        role = link_role(occurrence)
        render_counts[state] += 1
        role_counts[role] += 1
        artifact_counter = artifact_counts.setdefault(occurrence["artifact"], Counter())
        artifact_counter["url_occurrence_count"] += 1
        artifact_counter[state] += 1
        parsed = safe_parse(occurrence["url"])
        if parsed and parsed.hostname:
            host_counts[parsed.hostname.lower()] += 1
        if state != "https-allowlisted-renderable" and len(suppressed_samples) < 25:
            suppressed_samples.append({
                **{key: occurrence.get(key) for key in ("artifact", "json_path", "candidate_name", "source_key", "url")},
                "state": state,
                "reason": reason,
            })
        # Only links that survive the fail-closed rendering policy are eligible
        # to become clickable canonical TNS objects. Noncanonical source-native
        # URL strings remain counted as suppressed provenance.
        if state == "https-allowlisted-renderable" and is_declared_tns_object(occurrence):
            canonical, problem = validate_tns_object(occurrence)
            if problem:
                identity = occurrence.get("candidate_name") or occurrence.get("json_path")
                structural_problems.append(f"{identity}: {problem}")
            elif canonical:
                tns_links.append(canonical)

    audit = {
        "audit_version": AUDIT_VERSION,
        "artifact_counts": {
            name: dict(sorted(counts.items()))
            for name, counts in sorted(artifact_counts.items())
        },
        "url_occurrence_count": len(occurrences),
        "distinct_url_count": len({row["url"] for row in occurrences}),
        "checked_candidate_url_occurrence_count": sum(
            row["artifact"] == "candidate-catalog" for row in occurrences
        ),
        "checked_source_universe_url_occurrence_count": sum(
            row["artifact"] == "source-universe" for row in occurrences
        ),
        "renderability_counts": dict(sorted(render_counts.items())),
        "role_counts": {role: role_counts.get(role, 0) for role in ROLE_DEFINITIONS},
        "role_definitions": ROLE_DEFINITIONS,
        "host_occurrence_counts": dict(sorted(host_counts.items())),
        "suppressed_sample": suppressed_samples,
        "suppression_policy": (
            "Insecure, credential-bearing, explicit-port, malformed, and unallowlisted URLs "
            "remain provenance text but must not render as clickable public links."
        ),
    }
    return audit, tns_links, structural_problems


def structural_problems(snapshot: dict, universe: dict | None) -> tuple[list[str], list[dict]]:
    """Backward-compatible wrapper used by older focused checks."""
    _, tns_links, problems = audit_links(snapshot, universe)
    return problems, tns_links


def live_tns_check(sample: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        sample["url"],
        headers={
            "User-Agent": (
                "CTAS-public-link-check/2.0 "
                "(+https://jackmcguireastro.github.io/ctas.html)"
            )
        },
    )
    checked_at = stamp()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            body = response.read(700_000).decode("utf-8", errors="replace").lower()
            final_url = response.geturl()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            **sample, "checked_at": checked_at, "state": "degraded-provider-unavailable",
            "http_status": getattr(exc, "code", None), "reason": type(exc).__name__,
        }
    expected_tokens = {
        str(sample["object_id"]).lower(),
        str(sample["candidate_name"] or "").lower(),
    }
    page_not_found = "page not found" in body or "object not found" in body
    identity_present = any(token and token in body for token in expected_tokens)
    final = safe_parse(final_url)
    final_host = final.hostname if final else None
    if status == 200 and identity_present and not page_not_found and final_host == "www.wis-tns.org":
        state = "passed"
        reason = "expected object identity appears on the resolved TNS page"
    elif status in {403, 429, 500, 502, 503, 504}:
        state = "degraded-provider-unavailable"
        reason = "temporary provider or access response"
    else:
        state = "failed-wrong-object"
        reason = "resolved page did not establish the intended TNS object"
    return {**sample, "checked_at": checked_at, "state": state, "http_status": status,
            "resolved_url": final_url, "reason": reason}


def atomic_write(path: Path, raw: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, path)


def public_artifact_path(manifest_path: Path, public_path: str) -> Path:
    """Resolve a manifest's repository-relative path without allowing escape."""

    manifest_path = manifest_path.resolve()
    if manifest_path.parent.name != "candidate-chunks":
        raise ValueError("candidate manifest must live in a candidate-chunks directory")
    data_dir = manifest_path.parent.parent
    published = Path(public_path)
    try:
        relative = published.relative_to(Path("ctas/data"))
    except ValueError as exc:
        raise ValueError(f"public artifact is outside ctas/data: {public_path}") from exc
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"unsafe public artifact path: {public_path}")
    target = (data_dir / relative).resolve()
    if data_dir != target and data_dir not in target.parents:
        raise ValueError(f"public artifact escapes output root: {public_path}")
    return target


def load_partitioned_catalog(index_path: Path, manifest_path: Path) -> dict[str, Any]:
    """Verify and reconstruct every complete record from the public shards."""

    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    if manifest.get("schema") != "ctas.public-complete-catalog-manifest@1.0.0":
        raise ValueError("unsupported complete-catalog manifest schema")
    index_meta = manifest.get("catalog_index") or {}
    declared_index = public_artifact_path(manifest_path, str(index_meta.get("path") or ""))
    if declared_index != index_path.resolve():
        raise ValueError("catalog-index path does not match the complete-catalog manifest")
    index_raw = index_path.read_bytes()
    if index_meta.get("bytes") != len(index_raw):
        raise ValueError("catalog-index byte length does not match the manifest")
    if index_meta.get("sha256") != hashlib.sha256(index_raw).hexdigest():
        raise ValueError("catalog-index checksum does not match the manifest")
    index = json.loads(index_raw)
    index_rows = index.get("candidates")
    if not isinstance(index_rows, list):
        columns = index.get("candidate_columns")
        values = index.get("candidate_rows")
        if not isinstance(columns, list) or not isinstance(values, list) or "event_id" not in columns:
            raise ValueError("catalog index has no candidate table")
        if len(columns) != len(set(columns)):
            raise ValueError("catalog index candidate columns are not unique")
        index_rows = []
        for row in values:
            if not isinstance(row, list) or len(row) != len(columns):
                raise ValueError("catalog index candidate row width is invalid")
            index_rows.append(dict(zip(columns, row)))

    chunks = manifest.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != manifest.get("chunk_count"):
        raise ValueError("complete-catalog chunk count is inconsistent")
    paths = [str(row.get("path") or "") for row in chunks if isinstance(row, dict)]
    if len(paths) != len(chunks) or paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("complete-catalog chunk paths are not unique and ordered")

    complete_by_id: dict[str, dict[str, Any]] = {}
    for metadata in chunks:
        path = str(metadata["path"])
        chunk_path = public_artifact_path(manifest_path, path)
        raw = chunk_path.read_bytes()
        if metadata.get("bytes") != len(raw):
            raise ValueError(f"chunk byte length mismatch: {path}")
        if metadata.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError(f"chunk checksum mismatch: {path}")
        document = json.loads(raw)
        candidates = document.get("candidates")
        if not isinstance(candidates, list) or document.get("candidate_count") != len(candidates):
            raise ValueError(f"chunk candidate count mismatch: {path}")
        if metadata.get("candidate_count") != len(candidates):
            raise ValueError(f"manifest candidate count mismatch: {path}")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError(f"chunk contains a non-object candidate: {path}")
            event_id = str(candidate.get("event_id") or "")
            if not event_id or event_id in complete_by_id:
                raise ValueError(f"chunk contains a missing or duplicate event_id: {path}")
            complete_by_id[event_id] = candidate

    index_ids = [str(row.get("event_id") or "") for row in index_rows if isinstance(row, dict)]
    if (
        len(index_ids) != len(index_rows)
        or "" in index_ids
        or len(index_ids) != len(set(index_ids))
        or set(index_ids) != set(complete_by_id)
        or len(index_ids) != manifest.get("candidate_count")
        or len(index_ids) != index.get("candidate_count")
    ):
        raise ValueError("catalog-index and complete-chunk UUID sets do not match")
    ordered = [complete_by_id[event_id] for event_id in index_ids]
    canonical = (json.dumps(ordered, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if manifest.get("assembled_candidates_checksum_sha256") != hashlib.sha256(canonical).hexdigest():
        raise ValueError("reconstructed complete-catalog checksum does not match the manifest")
    return {**index, "candidates": ordered}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-index", default="ctas/data/catalog-index.json")
    parser.add_argument(
        "--candidate-manifest", default="ctas/data/candidate-chunks/manifest.json",
    )
    parser.add_argument("--source-universe", default="ctas/data/source-universe.json")
    parser.add_argument("--output", default="ctas/data/link-health.json")
    parser.add_argument("--live-if-stale-hours", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--no-live", action="store_true")
    args = parser.parse_args()

    index_path, manifest_path, universe_path, output_path = map(
        Path, (args.catalog_index, args.candidate_manifest, args.source_universe, args.output)
    )
    snapshot = load_partitioned_catalog(index_path, manifest_path)
    universe = json.loads(universe_path.read_text()) if universe_path.exists() else None
    recursive_audit, tns_links, problems = audit_links(snapshot, universe)
    previous = None
    if output_path.exists():
        try:
            previous = json.loads(output_path.read_text())
        except (OSError, json.JSONDecodeError):
            previous = None

    # Prefer the representative object already used for CTAS/TNS live review;
    # fall back to the first canonical object when it is not in the catalog.
    sample = next((row for row in tns_links if row["object_id"].lower() == "2026zke"), None)
    sample = sample or (tns_links[0] if tns_links else None)
    live_result = None
    previous_result = (previous or {}).get("representative_tns")
    previous_time = parse_time((previous_result or {}).get("checked_at"))
    cache_fresh = bool(
        previous_result and previous_time and
        previous_time >= datetime.now(UTC) - timedelta(hours=args.live_if_stale_hours) and
        previous_result.get("url") == (sample or {}).get("url")
    )
    if sample and not args.no_live and not cache_fresh:
        live_result = live_tns_check(sample, args.timeout)
    elif sample and cache_fresh:
        live_result = previous_result
    elif sample and args.no_live:
        live_result = {
            **sample,
            "checked_at": None,
            "state": "not-checked",
            "reason": "live check disabled",
        }

    live_state = (live_result or {}).get("state") or "not-checked"
    unique_tns = {row["object_id"] for row in tns_links}
    structural_status = "passed" if not problems and bool(tns_links) else "failed"
    report = {
        "schema": SCHEMA,
        "audit_version": AUDIT_VERSION,
        "checked_at": stamp(),
        "catalog_content_checksum_sha256": snapshot.get("catalog_content_checksum_sha256"),
        "structural_status": structural_status,
        "structural_problem_count": len(problems),
        "structural_problems": problems[:100],
        "recursive_external_link_audit": recursive_audit,
        # Kept for v1 readers; this now counts every recursive candidate-catalog
        # URL occurrence rather than only candidate.links rows.
        "checked_candidate_link_count": recursive_audit["checked_candidate_url_occurrence_count"],
        "canonical_tns_link_count": len(tns_links),
        "distinct_canonical_tns_object_count": len(unique_tns),
        # live_status remains as a compatibility alias. Its scope is explicitly
        # one sampled provider, never the complete external-link universe.
        "live_status": live_state,
        "live_status_scope": "sampled-provider-only",
        "sampled_provider_live_status": live_state,
        "live_provider_samples": [{"provider": "TNS", **live_result}] if live_result else [],
        "representative_tns": live_result,
        "claim_boundary": (
            "The recursive structural audit classifies every exported candidate/source URL "
            "and strictly validates canonical TNS object links. Only the listed TNS object "
            "page is sampled live; no live-health claim is made for unsampled providers. "
            "Suppressed URLs remain non-clickable provenance rather than structural failures."
        ),
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["report_checksum_sha256"] = hashlib.sha256(canonical).hexdigest()
    atomic_write(output_path, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    print(
        f"structural={structural_status} urls={recursive_audit['url_occurrence_count']} "
        f"tns_objects={len(unique_tns)} sampled_tns={live_state}"
    )
    return 1 if structural_status != "passed" or live_state == "failed-wrong-object" else 0


if __name__ == "__main__":
    raise SystemExit(main())
