#!/usr/bin/env python3
"""Validate public CTAS links and cautiously sample one live TNS object page."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

UTC = timezone.utc
SCHEMA = "ctas.link-health@1.0.0"
TNS_DESIGNATION = re.compile(r"^(?:AT|SN)?(\d{4}[a-z]+)$", re.IGNORECASE)
ALLOWED_HOSTS = {
    "api.fink-portal.org", "api.ztf.fink-portal.org", "apps.aavso.org",
    "archive.eso.org", "archive.stsci.edu", "asas-sn.osu.edu", "blackgem.org",
    "cgbm.calet.jp", "chime-experiment.ca", "doc.lsst.fink-broker.org",
    "docs.aavso.org", "ep.bao.ac.cn", "fallingstar-data.com", "gcn.gsfc.nasa.gov",
    "gcn.nasa.gov", "github.com", "goto-observatory.org", "heasarc.gsfc.nasa.gov",
    "irsa.ipac.caltech.edu", "lasair.readthedocs.io", "mast.stsci.edu",
    "maxi.riken.jp", "ned.ipac.caltech.edu", "observ.pereplet.ru",
    "outerspace.stsci.edu", "roc-2.icecube.wisc.edu", "roc.icecube.wisc.edu",
    "rubinobservatory.org", "simbad.cds.unistra.fr", "ui.adsabs.harvard.edu",
    "vizier.cds.unistra.fr", "wfst.bao.ac.cn", "www.aavso.org",
    "www.cosmos.esa.int", "www.wis-tns.org", "www.wiserep.org", "yse.ucsc.edu",
    "www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca", "archive.gemini.edu", "www.ivoa.net",
    "tom-toolkit.readthedocs.io", "ampelproject.github.io", "antares.noirlab.edu",
    "babamul.caltech.edu", "pitt-broker.readthedocs.io", "ztf.uw.edu",
}


def stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def structural_problems(snapshot: dict, universe: dict | None) -> tuple[list[str], list[dict]]:
    problems: list[str] = []
    tns_links: list[dict] = []
    for candidate in snapshot.get("candidates", []):
        for link in candidate.get("links", []):
            url = str(link.get("url") or "")
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
                problems.append(f"{candidate.get('name')}: unsafe link structure")
                continue
            if parsed.hostname not in ALLOWED_HOSTS:
                problems.append(f"{candidate.get('name')}: link origin is not allowlisted")
                continue
            if link.get("source_key") == "tns" or link.get("label") == "TNS":
                match = TNS_DESIGNATION.fullmatch(str(link.get("designation") or ""))
                expected = f"/object/{match.group(1)}" if match else None
                if not match or parsed.hostname != "www.wis-tns.org" or parsed.path != expected or parsed.query or parsed.fragment:
                    problems.append(f"{candidate.get('name')}: malformed TNS object link")
                else:
                    tns_links.append({
                        "candidate_name": candidate.get("name"),
                        "designation": link.get("designation"),
                        "object_id": match.group(1),
                        "url": url,
                        "ra_deg": candidate.get("ra_deg"),
                        "dec_deg": candidate.get("dec_deg"),
                    })
    for source in (universe or {}).get("sources", []):
        url = source.get("documentation_url")
        if not url:
            continue
        parsed = urlparse(str(url))
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
            problems.append(f"source {source.get('source_key')}: documentation origin is not allowlisted")
    return problems, tns_links


def live_tns_check(sample: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        sample["url"],
        headers={"User-Agent": "CTAS-public-link-check/1.0 (+https://jackmcguireastro.github.io/ctas.html)"},
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
    expected_tokens = {str(sample["object_id"]).lower(), str(sample["candidate_name"] or "").lower()}
    page_not_found = "page not found" in body or "object not found" in body
    identity_present = any(token and token in body for token in expected_tokens)
    final = urlparse(final_url)
    if status == 200 and identity_present and not page_not_found and final.hostname == "www.wis-tns.org":
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default="ctas/data/candidates.json")
    parser.add_argument("--source-universe", default="ctas/data/source-universe.json")
    parser.add_argument("--output", default="ctas/data/link-health.json")
    parser.add_argument("--live-if-stale-hours", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--no-live", action="store_true")
    args = parser.parse_args()

    candidates_path, universe_path, output_path = map(Path, (args.candidates, args.source_universe, args.output))
    snapshot = json.loads(candidates_path.read_text())
    universe = json.loads(universe_path.read_text()) if universe_path.exists() else None
    problems, tns_links = structural_problems(snapshot, universe)
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
        live_result = {**sample, "checked_at": None, "state": "degraded-provider-unavailable", "reason": "live check disabled"}

    live_state = (live_result or {}).get("state")
    report = {
        "schema": SCHEMA,
        "checked_at": stamp(),
        "catalog_content_checksum_sha256": snapshot.get("catalog_content_checksum_sha256"),
        "structural_status": "passed" if not problems and bool(tns_links) else "failed",
        "structural_problem_count": len(problems),
        "structural_problems": problems[:100],
        "checked_candidate_link_count": sum(len(candidate.get("links", [])) for candidate in snapshot.get("candidates", [])),
        "canonical_tns_link_count": len(tns_links),
        "live_status": live_state or "degraded-provider-unavailable",
        "representative_tns": live_result,
        "claim_boundary": "Structural validity is a publication gate. Temporary remote provider failure is degraded link health, not proof that a canonical link is broken.",
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["report_checksum_sha256"] = hashlib.sha256(canonical).hexdigest()
    atomic_write(output_path, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    print(f"structural={report['structural_status']} tns={len(tns_links)} live={report['live_status']}")
    return 1 if report["structural_status"] != "passed" or report["live_status"] == "failed-wrong-object" else 0


if __name__ == "__main__":
    raise SystemExit(main())
