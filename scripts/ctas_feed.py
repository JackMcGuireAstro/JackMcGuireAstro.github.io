#!/usr/bin/env python3
"""Feeds of CTAS public records: Atom 1.0 and JSON Feed 1.1, per category and per sky region.

Two products, both deterministic and without any network:

1. ctas/data/feed.xml (unchanged since 2026-09): strong new follow-up targets from the
   release's first-screen summary (ctas/data/live-summary.json), discovered in the last
   FEED_DAYS days relative to the newest discovery, CTAS score >= FEED_MIN_SCORE.

       ctas_feed.py [--summary ctas/data/live-summary.json] [--output ctas/data/feed.xml]

2. ctas/data/feeds/ (Framework v2 Track 1, 2026-10-19), written by the exporter from the
   complete compact index and indexed by ctas/data/feeds/index.json:

       feeds/all-events.{json,xml}                 every public record
       feeds/event-type/<event-type>.{json,xml}    one per event type the exporter enumerates
       feeds/channel/<ranking-channel>.{json,xml}  one per messenger family (ranking channel)
       feeds/region/healpix2-<nnn>.{json,xml}      one per HEALPix order-2 NESTED pixel in
                                                   ICRS (nside 4: 192 regions of about 215 square
                                                   degrees each, nnn = 000..191)
       feeds/region/galactic-plane.{json,xml}      |b| <= 10 degrees (J2000 galactic frame)
       feeds/region/north.{json,xml}               Dec >= 0 (equatorial northern sky)
       feeds/region/south.{json,xml}               Dec < 0

   Each feed holds the newest FEED_ITEMS records of its set, ordered by the record's
   public change clock: the latest of the provider discovery, classification, spectrum,
   messenger-notice and retraction clocks the compact index carries (never the export
   time, never the CTAS catalog update time), so a feed's bytes change only when a
   record in it does. Entry ids are the stable event ids (urn:ctas:event:<uuid>), so a
   reader shows a changed record as updated rather than as a second item. The region and
   category sets are fixed, so every feed address is stable from release to release; a
   feed whose set is empty is still written (valid, with no items).

   JSON Feed 1.1 (https://www.jsonfeed.org/version/1.1/): version, title, items[] with
   id and content_text; the per-item CTAS fields live under the "_ctas" extension
   object, as the specification requires for custom fields.

       ctas_feed.py --catalog-index ctas/data/catalog-index.json --feeds-dir ctas/data/feeds

   The HEALPix index comes from astropy_healpix (the cloud publisher installs it with the
   collector); the galactic latitude is the closed-form J2000 rotation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape

SITE = "https://jackmcguireastro.github.io"
FEED_URL = f"{SITE}/ctas/data/feed.xml"
FEED_DAYS = 30
FEED_MIN_SCORE = 60.0
FEED_LIMIT = 50
TARGET_ROLE = "follow-up-target-candidate"

FEEDS_SCHEMA = "ctas.public-feeds-index@1.0.0"
FEEDS_DIR = "ctas/data/feeds"
FEED_ITEMS = 100
HEALPIX_ORDER = 2
HEALPIX_NSIDE = 1 << HEALPIX_ORDER
HEALPIX_REGIONS = 12 * HEALPIX_NSIDE * HEALPIX_NSIDE
GALACTIC_PLANE_HALF_WIDTH_DEG = 10.0
# The exporter's event types (RECORD_ROLE_BY_EVENT_TYPE) and ranking channels; a static
# test keeps these in step with scripts/export_ctas_snapshot.py.
EVENT_TYPES = (
    "optical-transient", "x-ray-transient", "gamma-ray-transient", "gamma-ray-burst-candidate",
    "high-energy-trigger", "high-energy-count-rate-trigger", "fast-radio-burst",
    "high-energy-neutrino", "high-energy-neutrino-track", "high-energy-neutrino-cascade",
    "neutrino-gamma-coincidence-candidate", "terrestrial-gamma-ray-flash", "solar-flare",
)
RANKING_CHANNELS = ("optical", "x-ray-gamma-ray", "radio", "neutrino-multimessenger", "contextual-non-target")
CHANGE_CLOCKS = ("discovery_time", "latest_classification_at", "latest_spectrum_at", "latest_messenger_at", "latest_retraction_at")
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
# J2000 north galactic pole and the galactic longitude of the celestial pole.
_NGP_RA = math.radians(192.85948)
_NGP_DEC = math.radians(27.12825)


def _when(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------- feed.xml (2026-09)
def select_targets(summary: dict) -> list[dict]:
    columns = summary.get("candidate_columns") or []
    at = {name: index for index, name in enumerate(columns)}
    rows = []
    for raw in summary.get("candidate_rows") or []:
        row = {name: raw[index] if index < len(raw) else None for name, index in at.items()}
        discovered = _when(row.get("discovery_time"))
        score = row.get("ctas_score")
        if row.get("record_role") != TARGET_ROLE or discovered is None or not isinstance(score, (int, float)):
            continue
        row["_discovered"] = discovered
        rows.append(row)
    if not rows:
        return []
    newest = max(row["_discovered"] for row in rows)
    cutoff = newest - timedelta(days=FEED_DAYS)
    chosen = [row for row in rows if row["_discovered"] >= cutoff and row["ctas_score"] >= FEED_MIN_SCORE]
    chosen.sort(key=lambda row: (row["_discovered"], str(row.get("event_id"))), reverse=True)
    return chosen[:FEED_LIMIT]


def render(summary: dict) -> str:
    targets = select_targets(summary)
    updated = _stamp(max((row["_discovered"] for row in targets), default=EPOCH))
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        "  <title>CTAS — strong new follow-up targets</title>",
        f"  <subtitle>Follow-up target candidates from the Cowboy Transient Alert System discovered in the last {FEED_DAYS} days with a CTAS score of at least {FEED_MIN_SCORE:g}. Scores are triage aids, not classifications.</subtitle>",
        f'  <link rel="self" type="application/atom+xml" href="{FEED_URL}"/>',
        f'  <link rel="alternate" type="text/html" href="{SITE}/ctas.html"/>',
        f"  <id>{FEED_URL}</id>",
        f"  <updated>{updated}</updated>",
        "  <author><name>Jack McGuire (CTAS)</name></author>",
    ]
    for row in targets:
        event_id = str(row.get("event_id"))
        name = str(row.get("name") or event_id)
        label = row.get("classification") or "Unclassified"
        magnitude = row.get("discovery_magnitude")
        survey = row.get("discovery_survey")
        ra, dec = row.get("ra_deg"), row.get("dec_deg")
        bits = [str(label)]
        if isinstance(magnitude, (int, float)):
            bits.append(f"{magnitude:.1f} mag")
        if survey:
            bits.append(str(survey))
        title = f"{name} — {', '.join(bits)} (score {row['ctas_score']:.0f})"
        href = f"{SITE}/ctas.html?event={quote(event_id, safe='')}#dossier"
        details = [f"Discovered {_stamp(row['_discovered'])}", f"CTAS score {row['ctas_score']:.1f}"]
        if isinstance(ra, (int, float)) and isinstance(dec, (int, float)):
            details.append(f"RA {ra:.5f}°, Dec {dec:+.5f}°")
        if row.get("redshift") is not None:
            details.append(f"z = {row['redshift']}")
        out += [
            "  <entry>",
            f"    <title>{escape(title)}</title>",
            f'    <link rel="alternate" type="text/html" href="{escape(href)}"/>',
            f"    <id>urn:ctas:event:{escape(event_id)}</id>",
            f"    <updated>{_stamp(row['_discovered'])}</updated>",
            f"    <summary>{escape('; '.join(details))}.</summary>",
            "  </entry>",
        ]
    out.append("</feed>")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------ sky regions
def galactic_latitude_deg(ra_deg: float, dec_deg: float) -> float:
    """J2000 galactic latitude b of an ICRS position (closed form, degrees)."""
    ra, dec = math.radians(ra_deg), math.radians(dec_deg)
    sin_b = math.sin(dec) * math.sin(_NGP_DEC) + math.cos(dec) * math.cos(_NGP_DEC) * math.cos(ra - _NGP_RA)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_b))))


def healpix2_nested(ra_deg: float, dec_deg: float) -> int:
    """HEALPix order-2 NESTED pixel (0..191) of an ICRS position."""
    from astropy import units as u
    from astropy_healpix import lonlat_to_healpix
    return int(lonlat_to_healpix(float(ra_deg) * u.deg, float(dec_deg) * u.deg, HEALPIX_NSIDE, order="nested"))


def regions_for(ra_deg: Any, dec_deg: Any) -> list[str]:
    """The region feeds a position belongs to (none without coordinates)."""
    try:
        ra, dec = float(ra_deg), float(dec_deg)
    except (TypeError, ValueError):
        return []
    if not (math.isfinite(ra) and math.isfinite(dec) and 0 <= ra < 360 and -90 <= dec <= 90):
        return []
    regions = [f"healpix2-{healpix2_nested(ra, dec):03d}", "north" if dec >= 0 else "south"]
    if abs(galactic_latitude_deg(ra, dec)) <= GALACTIC_PLANE_HALF_WIDTH_DEG:
        regions.append("galactic-plane")
    return regions


def region_names() -> list[str]:
    return [f"healpix2-{index:03d}" for index in range(HEALPIX_REGIONS)] + ["galactic-plane", "north", "south"]


def region_description(name: str) -> str:
    if name.startswith("healpix2-"):
        return (f"Records inside HEALPix order-{HEALPIX_ORDER} NESTED pixel {int(name[9:])} (nside {HEALPIX_NSIDE}, "
                f"ICRS; about {41253 / HEALPIX_REGIONS:.0f} square degrees).")
    return {
        "galactic-plane": f"Records within {GALACTIC_PLANE_HALF_WIDTH_DEG:g} degrees of the galactic plane (J2000 galactic latitude).",
        "north": "Records with declination at or above 0 degrees (ICRS).",
        "south": "Records with declination below 0 degrees (ICRS).",
    }[name]


# ------------------------------------------------------------ per-record items
def index_rows(index: dict[str, Any]) -> list[dict[str, Any]]:
    columns = index.get("candidate_columns") or []
    rows = []
    for raw in index.get("candidate_rows") or []:
        rows.append({name: raw[position] if position < len(raw) else None for position, name in enumerate(columns)})
    return rows


def change_clock(row: dict[str, Any]) -> datetime | None:
    clocks = [moment for moment in (_when(row.get(key)) for key in CHANGE_CLOCKS) if moment is not None]
    return max(clocks) if clocks else None


def feed_item(row: dict[str, Any]) -> dict[str, Any]:
    event_id = str(row.get("event_id"))
    name = str(row.get("name") or event_id)
    label = str(row.get("classification") or "Unclassified")
    discovered = _when(row.get("discovery_time"))
    modified = change_clock(row) or discovered
    ra, dec = row.get("ra_deg"), row.get("dec_deg")
    score = row.get("ctas_score")
    lines = [
        f"{name}: {row.get('event_type') or 'event type not recorded'} via {row.get('primary_messenger') or 'messenger not recorded'}; "
        f"status {row.get('status') or 'not recorded'}; reported classification {label}.",
    ]
    if discovered is not None:
        lines.append(f"Discovered {_stamp(discovered)}" + (f" by {row['discovery_survey']}" if row.get("discovery_survey") else "")
                     + (f" at {row['discovery_magnitude']:.2f} mag (source reported)" if isinstance(row.get("discovery_magnitude"), (int, float)) else "") + ".")
    if isinstance(ra, (int, float)) and isinstance(dec, (int, float)):
        lines.append(f"ICRS RA {ra:.5f}°, Dec {dec:+.5f}°.")
    if isinstance(score, (int, float)):
        lines.append(f"CTAS score {score:.1f} (an ordering aid, not a probability or a classification).")
    for key, words in (("latest_classification_at", "latest classification"), ("latest_spectrum_at", "latest spectrum"),
                       ("latest_messenger_at", "latest messenger notice"), ("latest_retraction_at", "latest retraction")):
        moment = _when(row.get(key))
        if moment is not None:
            lines.append(f"{words[0].upper()}{words[1:]} {_stamp(moment)}.")
    lines.append("CTAS reports what public sources published; inclusion is not confirmation.")
    tags = [str(value) for value in (row.get("event_type"), row.get("primary_messenger"), row.get("ranking_channel")) if value]
    if label and label != "Unclassified":
        tags.append(label)
    item = {
        "id": f"urn:ctas:event:{event_id}",
        "url": f"{SITE}/ctas.html?event={quote(event_id, safe='')}#dossier",
        "title": f"{name} — {label}" + (f" (score {score:.0f})" if isinstance(score, (int, float)) else ""),
        "content_text": " ".join(lines),
        "tags": tags,
        "_ctas": {
            "event_id": event_id,
            "name": name,
            "event_type": row.get("event_type"),
            "primary_messenger": row.get("primary_messenger"),
            "ranking_channel": row.get("ranking_channel"),
            "record_role": row.get("record_role"),
            "status": row.get("status"),
            "classification": row.get("classification"),
            "ra_deg": ra if isinstance(ra, (int, float)) else None,
            "dec_deg": dec if isinstance(dec, (int, float)) else None,
            "ctas_score": score if isinstance(score, (int, float)) else None,
            "discovery_survey": row.get("discovery_survey"),
            "regions": regions_for(ra, dec),
            "dossier_chunk": f"{SITE}/ctas/data/{row['detail_chunk']}" if row.get("detail_chunk") else None,
        },
    }
    if discovered is not None:
        item["date_published"] = _stamp(discovered)
    if modified is not None:
        item["date_modified"] = _stamp(modified)
    return item


def _item_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (item.get("date_modified") or item.get("date_published") or "", item["id"])


# ------------------------------------------------------------ rendering
def feed_definitions() -> list[dict[str, str]]:
    """Every feed the release publishes: kind, name, title, description, file stem."""
    feeds = [{"kind": "all", "name": "all-events", "stem": "all-events",
              "title": "CTAS — all public records",
              "description": "Every public CTAS record, newest change first."}]
    for event_type in EVENT_TYPES:
        feeds.append({"kind": "event-type", "name": event_type, "stem": f"event-type/{event_type}",
                      "title": f"CTAS — {event_type.replace('-', ' ')} records",
                      "description": f"Public CTAS records whose event type is {event_type}, newest change first."})
    for channel in RANKING_CHANNELS:
        feeds.append({"kind": "channel", "name": channel, "stem": f"channel/{channel}",
                      "title": f"CTAS — {channel.replace('-', ' ')} channel",
                      "description": f"Public CTAS records in the {channel} ranking channel (messenger family), newest change first."})
    for region in region_names():
        feeds.append({"kind": "region", "name": region, "stem": f"region/{region}",
                      "title": f"CTAS — sky region {region}", "description": region_description(region)})
    return feeds


def _members(definition: dict[str, str], rows: list[dict[str, Any]], regions: dict[str, list[str]]) -> list[dict[str, Any]]:
    kind, name = definition["kind"], definition["name"]
    if kind == "all":
        return rows
    if kind == "event-type":
        return [row for row in rows if str(row.get("event_type") or "") == name]
    if kind == "channel":
        return [row for row in rows if str(row.get("ranking_channel") or "") == name]
    return [row for row in rows if name in regions.get(str(row.get("event_id")), [])]


def render_json_feed(definition: dict[str, str], items: list[dict[str, Any]]) -> str:
    document = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": definition["title"],
        "home_page_url": f"{SITE}/ctas.html",
        "feed_url": f"{SITE}/{FEEDS_DIR}/{definition['stem']}.json",
        "description": definition["description"] + " CTAS scores order review; they are not probabilities or classifications.",
        "authors": [{"name": "Jack McGuire (CTAS)", "url": f"{SITE}/"}],
        "language": "en",
        "items": items,
        "_ctas": {
            "kind": definition["kind"], "name": definition["name"], "item_limit": FEED_ITEMS,
            "atom_url": f"{SITE}/{FEEDS_DIR}/{definition['stem']}.xml",
            "index_url": f"{SITE}/{FEEDS_DIR}/index.json",
        },
    }
    return json.dumps(document, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def render_atom(definition: dict[str, str], items: list[dict[str, Any]]) -> str:
    self_url = f"{SITE}/{FEEDS_DIR}/{definition['stem']}.xml"
    updated = max((item.get("date_modified") or item.get("date_published") or "" for item in items), default="") or _stamp(EPOCH)
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>{escape(definition['title'])}</title>",
        f"  <subtitle>{escape(definition['description'])} CTAS scores order review; they are not probabilities or classifications.</subtitle>",
        f'  <link rel="self" type="application/atom+xml" href="{escape(self_url)}"/>',
        f'  <link rel="alternate" type="text/html" href="{SITE}/ctas.html"/>',
        f"  <id>{escape(self_url)}</id>",
        f"  <updated>{updated}</updated>",
        "  <author><name>Jack McGuire (CTAS)</name></author>",
    ]
    for item in items:
        out += [
            "  <entry>",
            f"    <title>{escape(item['title'])}</title>",
            f'    <link rel="alternate" type="text/html" href="{escape(item["url"])}"/>',
            f"    <id>{escape(item['id'])}</id>",
            f"    <updated>{escape(item.get('date_modified') or item.get('date_published') or _stamp(EPOCH))}</updated>",
        ]
        if item.get("date_published"):
            out.append(f"    <published>{escape(item['date_published'])}</published>")
        for tag in item.get("tags") or []:
            out.append(f'    <category term="{escape(tag, {chr(34): "&quot;"})}"/>')
        out += [f"    <summary>{escape(item['content_text'])}</summary>", "  </entry>"]
    out.append("</feed>")
    return "\n".join(out) + "\n"


def build_feeds(index: dict[str, Any], *, generated_at: str | None = None) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Every feed file (path relative to the site root -> bytes) and the index document."""

    rows = index_rows(index)
    items_by_id = {str(row.get("event_id")): feed_item(row) for row in rows}
    regions = {event_id: item["_ctas"]["regions"] for event_id, item in items_by_id.items()}
    files: dict[str, bytes] = {}
    listing: list[dict[str, Any]] = []
    for definition in feed_definitions():
        members = _members(definition, rows, regions)
        items = sorted((items_by_id[str(row.get("event_id"))] for row in members), key=_item_sort_key, reverse=True)[:FEED_ITEMS]
        json_path = f"{FEEDS_DIR}/{definition['stem']}.json"
        atom_path = f"{FEEDS_DIR}/{definition['stem']}.xml"
        json_raw = render_json_feed(definition, items).encode("utf-8")
        atom_raw = render_atom(definition, items).encode("utf-8")
        files[json_path] = json_raw
        files[atom_path] = atom_raw
        listing.append({
            "kind": definition["kind"], "name": definition["name"], "title": definition["title"],
            "description": definition["description"], "member_count": len(members), "item_count": len(items),
            "json_feed": {"path": json_path, "bytes": len(json_raw), "sha256": hashlib.sha256(json_raw).hexdigest()},
            "atom": {"path": atom_path, "bytes": len(atom_raw), "sha256": hashlib.sha256(atom_raw).hexdigest()},
        })
    index_document = {
        "schema": FEEDS_SCHEMA,
        "generated_at": generated_at or index.get("catalog_as_of"),
        "catalog_content_checksum_sha256": index.get("catalog_content_checksum_sha256"),
        "statement": (
            f"Atom 1.0 and JSON Feed 1.1 for every public CTAS record, per event type, per ranking channel "
            f"and per sky region; each feed holds its newest {FEED_ITEMS} records by public change clock. "
            "Feed bytes carry no export clock, so an unchanged feed is byte-identical between releases."
        ),
        "regions": {
            "healpix": {"order": HEALPIX_ORDER, "nside": HEALPIX_NSIDE, "scheme": "NESTED", "frame": "ICRS", "count": HEALPIX_REGIONS},
            "galactic_plane_half_width_deg": GALACTIC_PLANE_HALF_WIDTH_DEG,
            "hemispheres": "north: Dec >= 0; south: Dec < 0 (ICRS)",
        },
        "item_limit": FEED_ITEMS,
        "change_clocks": list(CHANGE_CLOCKS),
        "legacy_feed": {"path": "ctas/data/feed.xml", "title": "CTAS — strong new follow-up targets"},
        "feed_count": len(listing),
        "feeds": listing,
    }
    return files, index_document


def feed_problems(files: dict[str, bytes], index_document: dict[str, Any]) -> list[str]:
    """Structural checks a release must not ship without: JSON Feed required fields and
    well-formed Atom with feed id, updated and entry ids."""
    import xml.etree.ElementTree as ET
    problems: list[str] = []
    atom = "{http://www.w3.org/2005/Atom}"
    for row in index_document.get("feeds", []):
        for key, kind in (("json_feed", "json"), ("atom", "atom")):
            meta = row[key]
            raw = files.get(meta["path"])
            if raw is None or len(raw) != meta["bytes"] or hashlib.sha256(raw).hexdigest() != meta["sha256"]:
                problems.append(f"{meta['path']}: index metadata does not match the file")
                continue
            if kind == "json":
                try:
                    document = json.loads(raw)
                except ValueError:
                    problems.append(f"{meta['path']}: not JSON")
                    continue
                if document.get("version") != "https://jsonfeed.org/version/1.1" or not document.get("title") or not isinstance(document.get("items"), list):
                    problems.append(f"{meta['path']}: JSON Feed 1.1 requires version, title and items")
                ids = [item.get("id") for item in document.get("items", [])]
                if len(ids) != len(set(ids)) or any(not isinstance(item.get("id"), str) or not item["id"] for item in document.get("items", [])):
                    problems.append(f"{meta['path']}: every item needs a unique string id")
                if any(not (item.get("content_text") or item.get("content_html")) for item in document.get("items", [])):
                    problems.append(f"{meta['path']}: every item needs content_text or content_html")
                if len(document.get("items", [])) != row["item_count"]:
                    problems.append(f"{meta['path']}: item_count differs from the index")
            else:
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    problems.append(f"{meta['path']}: Atom is not well-formed XML")
                    continue
                if root.tag != f"{atom}feed" or root.find(f"{atom}id") is None or root.find(f"{atom}updated") is None or root.find(f"{atom}title") is None:
                    problems.append(f"{meta['path']}: Atom feed needs id, title and updated")
                entries = root.findall(f"{atom}entry")
                entry_ids = [(entry.find(f"{atom}id").text if entry.find(f"{atom}id") is not None else None) for entry in entries]
                if any(not value for value in entry_ids) or len(entry_ids) != len(set(entry_ids)):
                    problems.append(f"{meta['path']}: every Atom entry needs a unique id")
                if any(entry.find(f"{atom}updated") is None or entry.find(f"{atom}title") is None for entry in entries):
                    problems.append(f"{meta['path']}: every Atom entry needs title and updated")
                if len(entries) != row["item_count"]:
                    problems.append(f"{meta['path']}: entry count differs from the index")
    if index_document.get("feed_count") != len(index_document.get("feeds", [])):
        problems.append("feeds/index.json: feed_count differs from the listing")
    return problems


def write_feeds(index: dict[str, Any], site_root: Path, *, generated_at: str | None = None) -> dict[str, Any]:
    files, index_document = build_feeds(index, generated_at=generated_at)
    problems = feed_problems(files, index_document)
    if problems:
        raise ValueError("; ".join(problems[:5]))
    for relative, raw in files.items():
        target = site_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_bytes(raw)
        temporary.replace(target)
    index_path = site_root / FEEDS_DIR / "index.json"
    temporary = index_path.with_name("index.json.tmp")
    temporary.write_text(json.dumps(index_document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(index_path)
    return index_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summary", default="ctas/data/live-summary.json")
    parser.add_argument("--output", default="ctas/data/feed.xml")
    parser.add_argument("--catalog-index", default=None, help="write the per-category and per-region feeds from this compact index")
    parser.add_argument("--feeds-dir", default=FEEDS_DIR, help=f"where the feeds go, relative to the site root (default {FEEDS_DIR})")
    parser.add_argument("--site-root", default=".")
    args = parser.parse_args(argv)
    if args.catalog_index:
        if args.feeds_dir != FEEDS_DIR:
            raise SystemExit("the feeds directory is fixed at " + FEEDS_DIR)
        document = write_feeds(json.loads(Path(args.catalog_index).read_text(encoding="utf-8")), Path(args.site_root))
        print(f"feeds: {document['feed_count']} feeds (Atom and JSON Feed) -> {args.site_root}/{FEEDS_DIR}")
        return 0
    text = render(json.loads(Path(args.summary).read_text(encoding="utf-8")))
    output = Path(args.output)
    temporary = output.with_suffix(".xml.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"feed: {text.count('<entry>')} entries -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
