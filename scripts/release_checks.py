#!/usr/bin/env python3
"""Classified release checks: which failures block a release or a deploy, and which only warn.

Every release-time check of the two publishers (scripts/publish_ctas.sh,
scripts/publish_worldsindex.sh) and of the "Validate and deploy site" workflow
(scripts/deploy_checks.sh) has an id and a class in scripts/check-classes.json:

* ``always_block``: the hard core (secrets or banned keys in the public export, unmanifested
  files, checksum self-consistency). Every failure blocks, from the first run.
* ``integrity``: the published data, or the code that loads and verifies it, is wrong
  (manifest closure, certificate self-consistency, provenance closure, provider-label
  publishability, catastrophic data loss). A failure blocks once the check is out of
  watch mode.
* ``advisory``: everything else (layout, payload budgets, accessibility, page copy, tests
  that pin real objects by name, interop round trips of derived exports, feed and changelog
  formatting, tooling tests). A failure never blocks; it is a WARN line, a row in the run's
  results and one ``check-warning`` issue.

A check may name a ``parser`` and ``parts``: the failing parts it reports (unittest test
names, schema failures) are classified one by one, so a suite that is mostly integrity can
carry an advisory test (a regression event pinned by name) without that test ever
stopping a release. A check with ``per_schema`` (the two schema validators) is also split
into one sub-check per schema file, ``<id>:<file>``, each with its own watch period.

Watch mode. An integrity check, and each schema file of a ``per_schema`` check, is in watch
mode (its failures are warnings) until it was first seen at least 24 hours ago AND passed
N consecutive real releases (``watch.consecutive_passes``: CTAS 4, WorldsIndex 3, deploy 3).
The counts live in the small public ``check-history.json`` each publisher writes next to
its release (ctas/data/, worldsindex/data/) and reads back from the previous release, so
promotion needs no commit. A schema file whose sha256 (with the schemas it references)
differs from the recorded one starts a new watch period. Promotion is recorded and
permanent. Checks marked ``baseline`` ran on real releases before the history existed:
while no history is readable they count as promoted, so landing this machinery, or losing
the file, never turns an established guard into a warning.

Fallback. ``decide`` turns the check results, the classes and the commits into what
happens next: proceed, retry once with the code of the commit that produced the currently
published release, use that result, or block. ``choose`` picks the first deploy candidate
whose blocking checks pass. The publishers' cloud runners and the deploy workflow call them;
see RESILIENCE.md for the rules and the trade-offs.

Subcommands (nothing here talks to the network):

    record     --scope S --id ID --exit-code N [--output FILE] --results R.jsonl [--history H]
               classify one finished check; exit 3 when its failure blocks, else 0
    gates      --scope ctas-publish --certificate certification.json --results R.jsonl [--history H]
               record every CTAS certificate gate as a check; exit 3 when a failed gate blocks
    history    --scope S --results R.jsonl --previous OLD.json --out NEW.json [--site-commit SHA]
    decide     --results MAIN.jsonl --main-commit SHA [--release-commit SHA] [--fallback-results F.jsonl
               --fallback-exit N --fallback-classes FILE] --out D.json
               prints the action: proceed | try-fallback | use-fallback | block
    choose     --candidate LABEL=COMMIT=RESULTS.jsonl ... --out C.json   (the deploy's candidates, in order)
    blocking   --id ID   exit 0 when the check can block at all (not advisory), else 1
    checksums  --product ctas|worldsindex --root DIR    (the always_block checksum self-consistency check)
    count-drop --product ctas|worldsindex --current FILE --previous FILE [--max-drop 0.2]
    summary    --results R.jsonl   Markdown for a job summary
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_report import collect_secrets, sanitize  # noqa: E402  (same folder; the one sanitizer for every public text)

CLASSES_PATH = ROOT / "scripts" / "check-classes.json"
CLASSES_SCHEMA = "check-classes@2"
HISTORY_SCHEMA = "check-history@1"
RESULT_SCHEMA = "check-result@1"
DECISION_SCHEMA = "release-decision@1"
CHOICE_SCHEMA = "deploy-choice@1"
BLOCK_EXIT = 3
OUTPUT_TAIL_LINES = 60
UTC = dt.timezone.utc
CLASSES = ("always_block", "integrity", "advisory")
BLOCKING_CLASSES = ("always_block", "integrity")
HISTORY_PRUNE_DAYS = 30
DETAIL_CHARS = 300


# ----------------------------------------------------------------------------- basics
def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


def iso(moment: dt.datetime | None) -> str | None:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def parse_time(value: Any) -> dt.datetime | None:
    """ISO date or date-time (Z or offset) to aware UTC; a bare date is midnight UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return dt.datetime.fromisoformat(text).replace(tzinfo=UTC)
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def load_classes(path: str | Path | None = None) -> dict:
    document = json.loads(Path(path or CLASSES_PATH).read_text(encoding="utf-8"))
    problems = classes_problems(document)
    if problems:
        raise ValueError("check-classes.json: " + "; ".join(problems[:5]))
    return document


def classes_problems(document: dict) -> list[str]:
    """Structural problems of a check-classes document (empty when valid)."""
    problems = []
    if document.get("schema") != CLASSES_SCHEMA:
        problems.append(f"schema must be {CLASSES_SCHEMA}")
    watch = document.get("watch") or {}
    passes = watch.get("consecutive_passes")
    if not isinstance(passes, dict) or not all(isinstance(v, int) and v >= 1 for v in passes.values()):
        problems.append("watch.consecutive_passes must map each scope to a positive integer")
    checks = document.get("checks")
    if not isinstance(checks, dict) or not checks:
        return problems + ["checks must be a non-empty object"]
    for check_id, entry in checks.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", check_id):
            problems.append(f"{check_id}: id must be lower-case letters, digits, '.', '_' or '-'")
        if not isinstance(entry, dict) or entry.get("class") not in CLASSES:
            problems.append(f"{check_id}: class must be one of {CLASSES}")
            continue
        if not entry.get("scopes") or not all(isinstance(s, str) for s in entry["scopes"]):
            problems.append(f"{check_id}: scopes must list where it runs")
        if "since" in entry:
            problems.append(f"{check_id}: 'since' is gone; watch mode is read from check-history.json")
        if "baseline" in entry and entry["baseline"] is not True:
            problems.append(f"{check_id}: baseline is either true or absent")
        if "parts" in entry or "per_schema" in entry:
            if entry.get("parser") not in PARSERS:
                problems.append(f"{check_id}: parts and per_schema need a known parser ({', '.join(PARSERS)})")
            for rule in entry.get("parts", []):
                if not (isinstance(rule, list) and len(rule) == 2 and rule[1] in CLASSES):
                    problems.append(f"{check_id}: each part rule is [regex, class]")
                    continue
                try:
                    re.compile(rule[0])
                except re.error as error:
                    problems.append(f"{check_id}: bad regex {rule[0]!r}: {error}")
        for rule in entry.get("file_map", []):
            if not (isinstance(rule, list) and len(rule) == 2 and str(rule[1]).endswith(".schema.json")):
                problems.append(f"{check_id}: each file_map rule is [regex, schema file]")
    return problems


def watch_settings(classes: dict, scope: str | None) -> tuple[float, int]:
    watch = classes.get("watch") or {}
    passes = watch.get("consecutive_passes") or {}
    needed = passes.get(scope or "", max(passes.values()) if passes else 3)
    return float(watch.get("hours", 24)), int(needed)


# ----------------------------------------------------------------------------- parsers
def parse_unittest(text: str) -> list[str]:
    """'Class.test' for every FAIL:/ERROR: header of Python unittest output (3.10 to 3.13 forms)."""
    parts = []
    for match in re.finditer(r"^(?:FAIL|ERROR): (\w+) \(([\w.]+)\)", text, re.MULTILINE):
        test, dotted = match.group(1), match.group(2).split(".")
        owner = dotted[-2] if len(dotted) >= 2 and dotted[-1] == test else dotted[-1]
        part = f"{owner}.{test}"
        if part not in parts:
            parts.append(part)
    return parts


def parse_ctas_schemas(text: str) -> list[str]:
    """The text after FAIL on each failing line of scripts/validate_ctas_schemas.py."""
    parts = []
    for match in re.finditer(r"^FAIL\s+(.+)$", text, re.MULTILINE):
        part = " ".join(match.group(1).split())
        if part not in parts:
            parts.append(part)
    return parts


def parse_worldsindex_schemas(text: str) -> list[str]:
    """The failure lines scripts/validate_worldsindex_schemas.py lists under its summary FAIL
    line, and the text of any other FAIL line (the schema-directory checks that stop early)."""
    parts, collecting = [], False
    for line in text.splitlines():
        if line.startswith("FAIL  published JSON does not match"):
            collecting = True
            continue
        if line.startswith("FAIL  "):
            part = " ".join(line[6:].split())
            if part and part not in parts:
                parts.append(part)
            collecting = False
            continue
        if collecting:
            if line.startswith("  ") and not line.startswith("    "):
                part = line.strip()
                if part and part not in parts:
                    parts.append(part)
            elif not line.startswith("    "):
                collecting = False
    return parts


def parse_skipped_schemas(text: str) -> list[str]:
    """Schema files a validator reported as not applicable to this release ('skip  x.schema.json')."""
    return sorted(set(re.findall(r"^skip\s+([a-z0-9-]+\.schema\.json)\b", text, re.MULTILINE)))


PARSERS = {"unittest": parse_unittest, "ctas-schemas": parse_ctas_schemas,
           "worldsindex-schemas": parse_worldsindex_schemas}


def rule_class(entry: dict, text: str) -> str:
    for pattern, cls in entry.get("parts", []):
        if re.search(pattern, text):
            return cls
    return entry["class"]


def schema_of(entry: dict, part: str) -> str | None:
    """The schema file a failing part of a per_schema check is about, if it names one."""
    match = re.search(r"\b([a-z0-9][a-z0-9-]*\.schema\.json)\b", part)
    if match:
        return match.group(1)
    for pattern, schema in entry.get("file_map", []):
        if re.search(pattern, part):
            return schema
    return None


# ----------------------------------------------------------------------------- schema digests
def schema_digests(directory: str | Path) -> dict[str, str]:
    """sha256 of every *.schema.json under ``directory`` together with the schemas it
    references ($ref to a sibling file, transitively), so a change to common.schema.json
    also starts a new watch period for every schema that uses it."""
    folder = Path(directory)
    files = {path.name: path.read_bytes() for path in sorted(folder.glob("*.schema.json"))}
    refs = {name: set(re.findall(rb'"\$ref"\s*:\s*"([a-z0-9][a-z0-9-]*\.schema\.json)', raw)) for name, raw in files.items()}
    digests = {}
    for name in files:
        closure, stack = set(), [name]
        while stack:
            current = stack.pop()
            if current in closure or current not in files:
                continue
            closure.add(current)
            stack.extend(ref.decode() for ref in refs[current])
        digest = hashlib.sha256()
        for member in sorted(closure):
            digest.update(member.encode() + b"\0" + hashlib.sha256(files[member]).hexdigest().encode() + b"\n")
        digests[name] = digest.hexdigest()
    return digests


# ----------------------------------------------------------------------------- history
def history_rows(history: dict | None) -> dict:
    return ((history or {}).get("checks") or {}) if isinstance(history, dict) else {}


def history_known(history: dict | None, scope: str | None = None) -> bool:
    """A previous check history was read for ``scope`` (the owner of the check); its absence
    is the bootstrap case. Without a scope (or for documents that name none), any rows count."""
    if not history_rows(history):
        return False
    read = (history or {}).get("scopes_read")
    return True if scope is None or read is None else scope in read


def current_row(history: dict | None, check_id: str, sha256: str | None = None) -> dict:
    """The history row of a check, or {} when absent or recorded for another schema digest."""
    row = history_rows(history).get(check_id) or {}
    if row and sha256 and row.get("sha256") and row.get("sha256") != sha256:
        return {}
    return row


def mode_for(check_id: str, cls: str, entry: dict | None, history: dict | None, now: dt.datetime,
             hours: float, needed: int, sha256: str | None = None) -> dict:
    """{mode, reason}: mode is 'block' (always_block, or a promoted integrity check), 'watch'
    (integrity, not yet promoted) or 'warn' (advisory)."""
    if entry is None:
        return {"mode": "block", "reason": "unclassified check: treated as integrity until it is added to check-classes.json"}
    if cls == "always_block":
        return {"mode": "block", "reason": "always_block: never in watch mode"}
    if cls == "advisory":
        return {"mode": "warn", "reason": "advisory: never blocks"}
    row = current_row(history, check_id, sha256)
    changed = bool(sha256) and bool(history_rows(history).get(check_id)) and not row
    if row.get("promoted_at"):
        return {"mode": "block", "reason": f"promoted {row['promoted_at']}"}
    if not row:
        owner = (entry.get("scopes") or [None])[0]
        if not changed and not history_known(history, owner) and entry.get("baseline"):
            return {"mode": "block", "reason": "baseline check (ran on real releases before the check history); no history readable"}
        why = "schema changed: new watch period" if changed else "first seen in this run"
        return {"mode": "watch", "reason": f"watch: {why}; 0/{needed} consecutive real-release passes, needs {hours:g} h"}
    first = parse_time(row.get("first_seen")) or now
    passes = int(row.get("consecutive_passes") or 0)
    if now - first >= dt.timedelta(hours=hours) and passes >= needed:
        return {"mode": "block", "reason": f"promotion due: {passes} consecutive passes since {iso(first)}"}
    return {"mode": "watch", "reason": f"watch since {iso(first)}: {passes}/{needed} consecutive real-release passes; "
                                        f"promotion no earlier than {iso(first + dt.timedelta(hours=hours))}"}


def merge_histories(documents: Iterable[dict | None], classes: dict) -> dict:
    """One history view from several check-history documents (the publishers' and the
    deploy's): for each check the row from the document of the scope that owns it."""
    merged: dict[str, dict] = {}
    scopes_read: list[str] = []
    owners = {cid: (entry.get("scopes") or [None])[0] for cid, entry in classes["checks"].items()}
    for document in documents:
        if not isinstance(document, dict) or not history_rows(document):
            continue
        scope = document.get("scope")
        if scope and scope not in scopes_read:
            scopes_read.append(scope)
        for check_id, row in history_rows(document).items():
            owner = owners.get(check_id.split(":", 1)[0])
            if check_id not in merged or owner == scope:
                merged[check_id] = row
    return {"schema": HISTORY_SCHEMA, "scopes_read": scopes_read, "checks": merged}


def read_histories(paths: Iterable[str], classes: dict) -> dict:
    documents = []
    for path in paths:
        for single in str(path).split(os.pathsep):
            if single and Path(single).is_file():
                try:
                    documents.append(json.loads(Path(single).read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
    return merge_histories(documents, classes)


def failure_detail(result: dict) -> str | None:
    parts = [row["part"] for row in result.get("parts") or []]
    if parts:
        text = "; ".join(parts[:3])
    else:
        tail = [line for line in result.get("output_tail") or [] if line.strip()]
        text = tail[-1] if tail else f"exit {result.get('exit_code')}"
    return text[:DETAIL_CHARS]


def update_history(previous: dict | None, results: list[dict], classes: dict, scope: str,
                   now: dt.datetime, site_commit: str | None = None) -> dict:
    """The next check-history document for one scope, after a real release.

    Rows of checks this run did not evaluate are carried over unchanged (a release built by
    older or newer code keeps the others' counts) and dropped after 30 days without a run.
    A pass adds one to the consecutive count, a failure (warned or blocking) resets it and
    records a short sanitized detail, a skip leaves it. A changed schema digest starts a new
    row. Promotion happens here, once 24 hours passed since first_seen and the count reached
    N, and stays. On the very first history (bootstrap) baseline checks are seeded promoted.
    """
    hours, needed = watch_settings(classes, scope)
    bootstrap = not history_rows(previous)
    rows: dict[str, dict] = {}
    for check_id, row in history_rows(previous).items():
        seen = parse_time(row.get("last_seen")) or parse_time(row.get("first_seen"))
        if seen and now - seen <= dt.timedelta(days=HISTORY_PRUNE_DAYS):
            rows[check_id] = dict(row)
    latest: dict[str, dict] = {}
    for result in results:
        if result.get("scope") == scope and result.get("id"):
            latest[result["id"]] = result
    for check_id, result in latest.items():
        if result.get("outcome") == "skip":
            continue
        entry = classes["checks"].get(check_id.split(":", 1)[0])
        sha = result.get("schema_sha256")
        row = current_row({"checks": rows}, check_id, sha)
        if not row:
            row = {"first_seen": iso(now), "consecutive_passes": 0, "promoted_at": None}
            if sha:
                row["sha256"] = sha
            if bootstrap and entry and entry.get("baseline") and ":" not in check_id:
                row["promoted_at"] = iso(now)
                row["seeded"] = "baseline: ran on real releases before the check history existed"
        row["class"] = result.get("class") if result.get("outcome") != "pass" else result.get("check_class", row.get("class"))
        row["last_seen"] = iso(now)
        if result.get("outcome") == "pass":
            row["consecutive_passes"] = int(row.get("consecutive_passes") or 0) + 1
            row["last_result"] = "pass"
        else:
            row["consecutive_passes"] = 0
            row["last_result"] = "fail"
            row["last_failure_at"] = iso(now)
            row["last_failure_detail"] = failure_detail(result)
        if not row.get("promoted_at") and int(row["consecutive_passes"]) >= needed \
                and now - (parse_time(row.get("first_seen")) or now) >= dt.timedelta(hours=hours):
            row["promoted_at"] = iso(now)
        rows[check_id] = row
    return {"schema": HISTORY_SCHEMA, "scope": scope, "updated_at": iso(now), "site_commit": site_commit,
            "watch": {"hours": hours, "consecutive_passes": needed}, "checks": dict(sorted(rows.items()))}


# ----------------------------------------------------------------------------- one result
def base_result(check_id: str, scope: str, exit_code: int, cls: str, entry: dict | None, now: dt.datetime,
                seconds: float | None) -> dict:
    return {
        "schema": RESULT_SCHEMA, "id": check_id, "scope": scope, "at": iso(now), "exit_code": exit_code,
        "check_class": cls, "class": cls, "baseline": bool((entry or {}).get("baseline")),
        "fallback_eligible": entry is not None and bool(entry.get("fallback", True)),
        "prerequisite": bool((entry or {}).get("prerequisite")),
        "seconds": round(seconds, 1) if seconds is not None else None, "parts": [], "output_tail": [],
    }


def outcome_for(cls: str, mode: str, prerequisite: bool) -> str:
    if cls == "advisory":
        return "warn"
    if mode == "block" or prerequisite:
        return "block"
    return "warn"   # an integrity check still in watch mode


def evaluate(check_id: str, scope: str, exit_code: int, output: str, classes: dict, history: dict | None,
             now: dt.datetime, seconds: float | None = None, skip_reason: str | None = None,
             secret_values: Iterable[str] = (), schema_dir: str | Path | None = None) -> list[dict]:
    """The result records of one finished check: the check itself first, then (for a
    per_schema check) one record per schema file. Pure apart from reading the schema files."""
    entry = classes["checks"].get(check_id)
    hours, needed = watch_settings(classes, scope)
    own_class = (entry or {}).get("class", "integrity")
    result = base_result(check_id, scope, exit_code, own_class, entry, now, seconds)
    own_mode = mode_for(check_id, own_class, entry, history, now, hours, needed)
    result.update(mode=own_mode["mode"], reason=own_mode["reason"])
    if skip_reason:
        return [{**result, "outcome": "skip", "reason": skip_reason}]
    tail = [line[:500] for line in sanitize(output, secret_values).splitlines()[-OUTPUT_TAIL_LINES:]] if exit_code else []
    parser = PARSERS.get((entry or {}).get("parser") or "")
    names = parser(output) if (parser and exit_code) else []

    sub_rows: list[dict] = []
    if entry is not None and entry.get("per_schema"):
        folder = Path(schema_dir) if schema_dir else ROOT / entry["per_schema"]
        digests = schema_digests(folder) if folder.is_dir() else {}
        skipped = set(parse_skipped_schemas(output))
        failing: dict[str, list[str]] = {}
        unattributed = []
        for name in names:
            # A part an always_block rule matches (an undeclared file) is never attributed to a
            # schema file, so a schema's watch period or class can never soften it.
            schema = None if rule_class(entry, name) == "always_block" else schema_of(entry, name)
            if schema in digests:
                failing.setdefault(schema, []).append(name)
            else:
                unattributed.append(name)
        for schema, sha in sorted(digests.items()):
            sub_id = f"{check_id}:{schema}"
            cls = rule_class(entry, schema)
            sub = base_result(sub_id, scope, 1 if schema in failing else 0, cls, entry, now, None)
            sub.update(family=check_id, schema_sha256=sha)
            mode = mode_for(sub_id, cls, entry, history, now, hours, needed, sha256=sha)
            sub.update(mode=mode["mode"], reason=mode["reason"])
            if schema in failing:
                sub.update(outcome=outcome_for(cls, mode["mode"], False),
                           parts=[{"part": part[:300], "class": cls} for part in failing[schema]])
            elif schema in skipped:
                sub.update(outcome="skip", reason="not applicable to this release (validator skipped it)")
            else:
                sub.update(outcome="pass")
            sub_rows.append(sub)
        if exit_code == 0 and not names:
            return [{**result, "outcome": "pass"}, *sub_rows]
        # Failing parts that name no schema file (the metaschema check, the feeds, an undeclared
        # file), or the whole check when nothing could be attributed (a crash): the family's
        # own watch mode applies, with the class of the matching part rule.
        own = [{"part": name[:300], "class": rule_class(entry, name)} for name in unattributed]
        if not own and not any(sub["outcome"] in ("warn", "block") for sub in sub_rows):
            own = [{"part": "the validator failed without naming a schema file", "class": own_class}]
        for part in own:
            mode = mode_for(check_id, part["class"], entry, history, now, hours, needed)
            part["outcome"] = outcome_for(part["class"], mode["mode"], result["prerequisite"])
        failed_subs = [sub for sub in sub_rows if sub["outcome"] in ("warn", "block")]
        parts = own + [{**part, "schema": sub["id"].split(":", 1)[1], "mode": sub["mode"], "outcome": sub["outcome"]}
                       for sub in failed_subs for part in sub["parts"]]
        blocking = [part for part in parts if part["outcome"] == "block"]
        outcome = "block" if blocking else "warn"
        failing_classes = {part["class"] for part in (blocking or parts)}
        effective = next(cls for cls in CLASSES if cls in failing_classes)
        if blocking:
            reason = "blocking: " + "; ".join(sorted({part.get("schema") or part["part"][:80] for part in blocking}))
        else:
            reason = "no failure blocks (advisory parts, or schema files in watch mode)"
        return [{**result, "outcome": outcome, "class": effective, "mode": "block" if blocking else own_mode["mode"],
                 "reason": reason, "parts": parts[:60], "output_tail": tail}, *sub_rows]

    if exit_code == 0:
        return [{**result, "outcome": "pass"}]
    if not names or entry is None:
        return [{**result, "outcome": outcome_for(own_class, own_mode["mode"], result["prerequisite"]), "output_tail": tail}]
    parts = [{"part": name[:300], "class": rule_class(entry, name)} for name in names]
    effective = next((cls for cls in CLASSES if any(p["class"] == cls for p in parts)), own_class)
    mode = own_mode if effective == own_class else mode_for(check_id, effective, entry, history, now, hours, needed)
    return [{**result, "outcome": outcome_for(effective, mode["mode"], result["prerequisite"]), "class": effective,
             "mode": mode["mode"], "reason": mode["reason"], "parts": parts, "output_tail": tail}]


def verdict_line(result: dict) -> str:
    parts = [row["part"] for row in result.get("parts") or [] if row.get("class") == result["class"]][:3]
    detail = f" ({'; '.join(parts)})" if parts else ""
    if result["outcome"] == "pass":
        return f"check {result['id']}: passed"
    if result["outcome"] == "skip":
        return f"check {result['id']}: skipped ({result['reason']})"
    if result["outcome"] == "warn":
        why = "advisory" if result["class"] == "advisory" else f"{result['class']}, watch mode"
        return f"WARN  check {result['id']} failed{detail}: {why}, not blocking [{result['reason']}]"
    return f"FAIL  check {result['id']} failed{detail}: {result['class']}, blocking [{result['reason']}]"


def append_results(path: str | Path, results: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, sort_keys=True) + "\n")


def read_results(path: str | Path | None) -> list[dict]:
    if not path or not Path(path).is_file():
        return []
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def top_level(results: list[dict]) -> list[dict]:
    """The checks themselves, without the per-schema sub-check rows."""
    return [row for row in results if not row.get("family")]


# ----------------------------------------------------------------------------- the decision
def code_fallback_record(decision: dict) -> dict | None:
    """The additive ``code_fallback`` status key of a release built by the fallback code."""
    if decision.get("action") != "use-fallback":
        return None
    return {
        "failed_commit": decision.get("main_commit"),
        "failed_check": (decision.get("blocking") or [None])[0],
        "failed_checks": list(decision.get("blocking") or []),
        "detail": decision.get("blocking_detail") or decision.get("explanation"),
        "used_commit": decision.get("release_commit"),
    }


def decide(main_results: list[dict], *, main_commit: str | None, now: dt.datetime,
           release_commit: str | None = None, release_commit_on_main: bool | None = None,
           main_exit: int | None = None, fallback_results: list[dict] | None = None,
           fallback_exit: int | None = None, fallback_check_ids: Iterable[str] | None = None) -> dict:
    """What happens after the checks ran with the current site code. Pure function.

    ``release_commit`` is the website commit that produced the currently published release
    (read back from that release). ``release_commit_on_main`` is False when that commit is not
    an ancestor of main (a release built from another branch must never become the fallback).

    proceed       no blocking failure: publish/deploy with the current code (warnings are recorded)
    try-fallback  blocking failures, all fallback-eligible, the release commit differs from the
                  current commit, is on main and knows every blocking check: run the release step
                  once more with that code on the same data
    use-fallback  the fallback run passed: its result is what gets published/deployed
    block         nothing is published/deployed; ``reason`` says why
    """
    main_results = top_level(main_results)
    blocking = [row for row in main_results if row.get("outcome") == "block"]
    warnings = [row for row in main_results if row.get("outcome") == "warn"]
    detail = None
    if blocking:
        first = blocking[0]
        parts = [p["part"] for p in first.get("parts") or [] if p.get("class") in BLOCKING_CLASSES][:3]
        tail = [line for line in first.get("output_tail") or [] if line.strip()]
        detail = ("; ".join(parts) if parts else (tail[-1] if tail else f"exit {first.get('exit_code')}"))[:DETAIL_CHARS]
    base = {
        "schema": DECISION_SCHEMA, "decided_at": iso(now), "main_commit": main_commit,
        "release_commit": release_commit, "blocking": [row["id"] for row in blocking],
        "blocking_detail": detail, "warnings": [row["id"] for row in warnings], "fallback": False,
    }

    def out(action, reason, text, **extra):
        return {**base, "action": action, "reason": reason, "explanation": text, **extra}

    if not blocking:
        if main_exit not in (None, 0, BLOCK_EXIT):
            return out("block", "error-outside-checks",
                       f"the run failed outside the classified checks (exit {main_exit}); fallback does not apply")
        return out("proceed", "no-blocking-failure",
                   f"no blocking failure; {len(warnings)} warning(s) recorded" if warnings else "every check passed")
    ids = ", ".join(base["blocking"])
    ineligible = [row["id"] for row in blocking if not row.get("fallback_eligible", True)]
    if ineligible:
        return out("block", "not-fallback-eligible",
                   f"{', '.join(ineligible)} never fall back (secrets, export safety, restore safety, or builder code site code cannot change)")
    if not release_commit:
        return out("block", "no-release-commit", f"{ids} failed and the published release does not record the commit that produced it")
    if main_commit and release_commit == main_commit:
        return out("block", "release-commit-is-current",
                   f"{ids} failed with the same code that produced the published release: the data or the environment changed")
    if release_commit_on_main is False:
        return out("block", "release-commit-not-on-main",
                   f"{ids} failed and the published release's commit {release_commit[:10]} is not an ancestor of main")
    if fallback_check_ids is not None:
        known = set(fallback_check_ids)
        unknown = [row["id"] for row in blocking if row["id"] not in known]
        if unknown:
            return out("block", "new-check-not-in-fallback-code",
                       f"{', '.join(unknown)} is unknown to the fallback code, which could only skip it, never answer it")
    if fallback_results is None:
        return out("try-fallback", "retry-with-release-commit",
                   f"{ids} failed with {(main_commit or '')[:10]}; retrying once with {release_commit[:10]}, the code that produced the published release")
    fallback_results = top_level(fallback_results)
    fallback_blocking = [row["id"] for row in fallback_results if row.get("outcome") == "block"]
    if not fallback_blocking and fallback_exit in (None, 0):
        return out("use-fallback", "fallback-passed",
                   f"{ids} failed with {(main_commit or '')[:10]}; the same data passed with {release_commit[:10]}",
                   fallback=True, fallback_blocking=[])
    same = sorted(set(base["blocking"]) & set(fallback_blocking))
    if same:
        return out("block", "data-integrity",
                   f"the fallback code reports the same blocking failure ({', '.join(same)}): the data itself is bad",
                   fallback_blocking=fallback_blocking)
    if fallback_blocking:
        return out("block", "fallback-failed", f"main failed {ids}; the fallback code failed {', '.join(fallback_blocking)}",
                   fallback_blocking=fallback_blocking)
    return out("block", "fallback-error", f"main failed {ids}; the fallback run ended with exit {fallback_exit}",
               fallback_blocking=[])


def choose(candidates: list[dict], *, max_candidates: int = 3) -> dict:
    """The deploy's choice of site code. Pure function.

    ``candidates`` in order of preference: [{label, commit, results: [...] | None, note?}];
    results None means the candidate was not validated (it could not be prepared). Candidates
    whose commit equals an earlier one are skipped; at most ``max_candidates`` are considered.
    The first candidate that ran and has no blocking result is chosen; advisory failures and
    watch-mode warnings are only warnings.
    """
    tried, seen = [], set()
    chosen = None
    for candidate in candidates:
        commit = candidate.get("commit")
        row = {"label": candidate.get("label"), "commit": commit}
        if not commit:
            tried.append({**row, "status": "unavailable", "note": candidate.get("note") or "no commit"})
            continue
        if commit in seen:
            tried.append({**row, "status": "duplicate", "note": "same commit as an earlier candidate"})
            continue
        if len(seen) >= max_candidates:
            tried.append({**row, "status": "not-tried", "note": f"at most {max_candidates} candidates"})
            continue
        seen.add(commit)
        results = candidate.get("results")
        if results is None:
            tried.append({**row, "status": "not-validated", "note": candidate.get("note") or "could not be prepared"})
            continue
        results = top_level(results)
        blocking = [r["id"] for r in results if r.get("outcome") == "block"]
        warnings = [r["id"] for r in results if r.get("outcome") == "warn"]
        status = "blocked" if blocking or not results else "passed"
        tried.append({**row, "status": status, "blocking": blocking, "warnings": warnings,
                      **({"note": "no check ran"} if not results else {})})
        if status == "passed":
            chosen = {**row, "warnings": warnings}
            break
    first = tried[0] if tried else {}
    main_failures = first.get("blocking", []) if first.get("label") == "main" else []
    return {"schema": CHOICE_SCHEMA, "chosen": chosen, "tried": tried, "main_blocking": main_failures,
            "fallback": bool(chosen) and chosen.get("label") != "main"}


def summary_markdown(results: list[dict], title: str = "Release checks") -> str:
    results = top_level(results)
    counts = {key: sum(1 for row in results if row["outcome"] == key) for key in ("pass", "warn", "block", "skip")}
    lines = [f"**{title}:** {counts['pass']} passed, {counts['warn']} warning(s), {counts['block']} blocking, {counts['skip']} skipped"]
    for row in results:
        if row["outcome"] in ("warn", "block"):
            parts = ", ".join(f"`{p['part'][:120]}`" for p in row["parts"][:4])
            lines.append(f"- {'warning' if row['outcome'] == 'warn' else 'BLOCKING'}: `{row['id']}` ({row['class']}, {row['mode']})"
                         + (f": {parts}" if parts else ""))
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- data checks
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def checksum_problems(product: str, root: str | Path) -> tuple[list[str], int]:
    """Files whose bytes differ from what the release's own checksum document states.

    CTAS: every ``ctas/data/`` file the certificate lists (the code files it binds are the
    deploy's code-binding question, not data self-consistency). WorldsIndex: every artifact
    the manifest declares. Returns (problems, files checked)."""
    root = Path(root)
    problems, checked = [], 0
    if product == "ctas":
        document = json.loads((root / "ctas/data/certification.json").read_text(encoding="utf-8"))
        listed = {path: row for path, row in (document.get("files") or {}).items() if path.startswith("ctas/data/")}
        if not listed:
            return ["the certificate lists no ctas/data file"], 0
        base = root
    elif product == "worldsindex":
        document = json.loads((root / "worldsindex/data/manifest.json").read_text(encoding="utf-8"))
        listed = document.get("artifacts") or {}
        if not listed:
            return ["the manifest declares no artifact"], 0
        base = root / "worldsindex/data"
    else:
        raise ValueError(f"unknown product {product}")
    for path, row in sorted(listed.items()):
        target = base / path
        expected = row.get("sha256") if isinstance(row, dict) else row
        if not target.is_file():
            problems.append(f"{path}: listed but missing")
            continue
        checked += 1
        if isinstance(row, dict) and isinstance(row.get("bytes"), int) and target.stat().st_size != row["bytes"]:
            problems.append(f"{path}: {target.stat().st_size} bytes, the release states {row['bytes']}")
            continue
        if sha256_file(target) != expected:
            problems.append(f"{path}: sha256 differs from the one the release states")
    return problems, checked


def release_count(product: str, document: dict) -> int | None:
    value = document.get("candidate_count") if product == "ctas" else document.get("objectCount")
    return value if isinstance(value, int) and value >= 0 else None


def count_drop_problem(product: str, current: dict | None, previous: dict | None, max_drop: float = 0.2) -> tuple[str | None, str]:
    """(problem or None, explanation): the release's count against the previous release's."""
    now = release_count(product, current or {})
    before = release_count(product, previous or {})
    noun = "candidates" if product == "ctas" else "objects"
    if now is None:
        return f"the new release states no {noun} count", "unreadable"
    if not before:
        return None, f"{now} {noun}; no previous count to compare with"
    drop = (before - now) / before
    text = f"{now} {noun} against {before} in the previous release ({-drop * 100:+.1f}%)"
    if drop > max_drop:
        return f"{text}: more than {max_drop * 100:g}% lost", text
    return None, text


# ----------------------------------------------------------------------------- CLI
def _now(args) -> dt.datetime:
    return parse_time(args.now) if getattr(args, "now", None) else now_utc()


def _annotate(result: dict) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or result["outcome"] not in ("warn", "block"):
        return
    kind = "warning" if result["outcome"] == "warn" else "error"
    print(f"::{kind} title=check {result['id']}::{verdict_line(result)}")


def per_schema_dir(classes: dict, check_id: str, tree: str | None) -> Path | None:
    entry = classes["checks"].get(check_id) or {}
    if not entry.get("per_schema") or not tree:
        return None
    return Path(tree) / entry["per_schema"]


def cmd_record(args) -> int:
    classes = load_classes(args.classes)
    history = read_histories(args.history or [], classes)
    output = ""
    if args.output and Path(args.output).is_file():
        output = Path(args.output).read_text(encoding="utf-8", errors="replace")
    if args.message:
        output = (output + "\n" + args.message).strip()
    rows = evaluate(args.id, args.scope, args.exit_code, output, classes, history, _now(args),
                    seconds=args.seconds, skip_reason=args.skip, schema_dir=per_schema_dir(classes, args.id, args.tree),
                    secret_values=collect_secrets(os.environ.get("CHECK_SECRET_ENV"), []))
    append_results(args.results, rows)
    print(verdict_line(rows[0]))
    for sub in rows[1:]:
        if sub["outcome"] in ("warn", "block"):
            print("      " + verdict_line(sub))
    _annotate(rows[0])
    return BLOCK_EXIT if rows[0]["outcome"] == "block" else 0


def cmd_gates(args) -> int:
    classes = load_classes(args.classes)
    history = read_histories(args.history or [], classes)
    now = _now(args)
    try:
        certificate = json.loads(Path(args.certificate).read_text(encoding="utf-8"))
        gates = certificate["gates"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        rows = evaluate("ctas.export", args.scope, 1, f"certificate unreadable: {error}", classes, history, now)
        append_results(args.results, rows)
        print(verdict_line(rows[0]))
        return BLOCK_EXIT
    blocked = False
    for gate in gates:
        passed = gate.get("passed") is True
        rows = evaluate(f"ctas.gate.{gate.get('id')}", args.scope, 0 if passed else 1,
                        "" if passed else f"gate {gate.get('id')}: {gate.get('evidence')}", classes, history, now)
        append_results(args.results, rows)
        if not passed:
            print(verdict_line(rows[0]))
            _annotate(rows[0])
        blocked = blocked or rows[0]["outcome"] == "block"
    print(f"certificate gates: {sum(1 for g in gates if g.get('passed') is True)}/{len(gates)} passed")
    return BLOCK_EXIT if blocked else 0


def cmd_history(args) -> int:
    classes = load_classes(args.classes)
    previous = read_histories([args.previous] if args.previous else [], classes)
    document = update_history(previous, read_results(args.results), classes, args.scope, _now(args), args.site_commit)
    Path(args.out).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    hours, needed = watch_settings(classes, args.scope)
    watching = sorted(cid for cid, row in document["checks"].items() if not row.get("promoted_at")
                      and (row.get("class") or (classes["checks"].get(cid.split(":", 1)[0]) or {}).get("class")) == "integrity")
    print(f"check history: {len(document['checks'])} check(s) recorded; {len(watching)} integrity check(s) still in watch "
          f"mode (promotion after {hours:g} h and {needed} consecutive passes)" + (f": {', '.join(watching)}" if watching else ""))
    return 0


def cmd_decide(args) -> int:
    known = None
    if args.fallback_classes:
        try:
            known = set(json.loads(Path(args.fallback_classes).read_text(encoding="utf-8"))["checks"])
        except (OSError, ValueError, KeyError, TypeError):
            # Fallback code older than the classification: it runs (and dies on) every check
            # that existed before the check history, i.e. the baseline checks.
            known = {cid for cid, entry in load_classes(args.classes)["checks"].items() if entry.get("baseline")}
    on_main = {"yes": True, "no": False}.get(args.release_commit_on_main or "")
    decision = decide(read_results(args.results), main_commit=args.main_commit, now=_now(args),
                      release_commit=args.release_commit or None, release_commit_on_main=on_main,
                      main_exit=args.main_exit,
                      fallback_results=read_results(args.fallback_results) if args.fallback_results else None,
                      fallback_exit=args.fallback_exit, fallback_check_ids=known)
    decision["scope"] = args.scope
    decision["code_fallback"] = code_fallback_record(decision)
    if args.out:
        Path(args.out).write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"action={decision['action']}\nreason={decision['reason']}\nfallback={str(decision['fallback']).lower()}\n")
    print(decision["action"])
    print(f"decision: {decision['action']} ({decision['reason']}): {decision['explanation']}", file=sys.stderr)
    return 0


def cmd_choose(args) -> int:
    candidates = []
    for item in args.candidate:
        label, _, rest = item.partition("=")
        commit, _, results = rest.partition("=")
        candidates.append({"label": label, "commit": commit or None,
                           "results": read_results(results) if results and Path(results).is_file() else None})
    choice = choose(candidates, max_candidates=args.max_candidates)
    if args.out:
        Path(args.out).write_text(json.dumps(choice, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    chosen = choice["chosen"]
    print(f"{chosen['label']} {chosen['commit']}" if chosen else "none")
    for row in choice["tried"]:
        print(f"  candidate {row['label']} {(row.get('commit') or '-')[:10]}: {row['status']}"
              + (f" (blocking: {', '.join(row['blocking'])})" if row.get("blocking") else "")
              + (f" ({row['note']})" if row.get("note") else ""), file=sys.stderr)
    return 0


def cmd_blocking(args) -> int:
    """Exit 0 when the check can block at all: always_block or integrity (whole or in part)."""
    try:
        classes = load_classes(args.classes)
    except (OSError, ValueError):
        return 0
    entry = classes["checks"].get(args.id)
    if entry is None or entry["class"] in BLOCKING_CLASSES:
        return 0
    return 0 if any(rule[1] in BLOCKING_CLASSES for rule in entry.get("parts", [])) else 1


def cmd_checksums(args) -> int:
    try:
        problems, checked = checksum_problems(args.product, args.root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"FAIL  the release's checksum document is unreadable: {error}")
        return 1
    if problems:
        print(f"FAIL  {len(problems)} of {checked} file(s) differ from the checksums the release states:")
        for line in problems[:40]:
            print("  " + line)
        return 1
    print(f"ok    {checked} file(s) match the checksums the release states")
    return 0


def cmd_count_drop(args) -> int:
    def load(path):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8")) if path and Path(path).is_file() else None
        except (OSError, ValueError):
            return None
    problem, text = count_drop_problem(args.product, load(args.current), load(args.previous), args.max_drop)
    if problem:
        print(f"FAIL  catastrophic data loss: {problem}")
        return 1
    print(f"ok    {text}")
    return 0


def cmd_summary(args) -> int:
    sys.stdout.write(summary_markdown(read_results(args.results), args.title))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, results=True):
        p.add_argument("--classes", default=str(CLASSES_PATH))
        p.add_argument("--now", help="ISO time override (tests)")
        if results:
            p.add_argument("--results", required=True, help="JSON Lines file of check results")

    p = sub.add_parser("record")
    common(p)
    p.add_argument("--scope", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--exit-code", type=int, default=0)
    p.add_argument("--output", help="file holding the check's output")
    p.add_argument("--message", help="extra failure text")
    p.add_argument("--seconds", type=float)
    p.add_argument("--skip", help="record the check as skipped, with this reason")
    p.add_argument("--tree", help="the checkout the check ran in; a per_schema check hashes <tree>/<per_schema> (default: this script's checkout)")
    p.add_argument("--history", action="append", help="check-history.json (repeatable, or os.pathsep-separated)")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("gates")
    common(p)
    p.add_argument("--scope", default="ctas-publish")
    p.add_argument("--certificate", required=True)
    p.add_argument("--history", action="append")
    p.set_defaults(func=cmd_gates)

    p = sub.add_parser("history")
    common(p)
    p.add_argument("--scope", required=True)
    p.add_argument("--previous")
    p.add_argument("--out", required=True)
    p.add_argument("--site-commit")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("decide")
    common(p)
    p.add_argument("--scope", default="")
    p.add_argument("--main-commit")
    p.add_argument("--main-exit", type=int)
    p.add_argument("--release-commit", help="the commit that produced the currently published release")
    p.add_argument("--release-commit-on-main", choices=["yes", "no", "unknown"], default="unknown")
    p.add_argument("--fallback-classes", help="the fallback code's check-classes.json (which checks it knows)")
    p.add_argument("--fallback-results")
    p.add_argument("--fallback-exit", type=int)
    p.add_argument("--out")
    p.add_argument("--github-output")
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("choose")
    common(p, results=False)
    p.add_argument("--candidate", action="append", default=[], help="LABEL=COMMIT=RESULTS.jsonl, in order of preference")
    p.add_argument("--max-candidates", type=int, default=3)
    p.add_argument("--out")
    p.set_defaults(func=cmd_choose)

    p = sub.add_parser("blocking")
    common(p, results=False)
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_blocking)

    p = sub.add_parser("checksums")
    p.add_argument("--product", required=True, choices=["ctas", "worldsindex"])
    p.add_argument("--root", default=".")
    p.set_defaults(func=cmd_checksums)

    p = sub.add_parser("count-drop")
    p.add_argument("--product", required=True, choices=["ctas", "worldsindex"])
    p.add_argument("--current", required=True)
    p.add_argument("--previous")
    p.add_argument("--max-drop", type=float, default=0.2)
    p.set_defaults(func=cmd_count_drop)

    p = sub.add_parser("summary")
    common(p)
    p.add_argument("--title", default="Release checks")
    p.set_defaults(func=cmd_summary)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
