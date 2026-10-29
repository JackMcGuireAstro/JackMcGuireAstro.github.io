#!/usr/bin/env python3
"""The CTAS observer tools' drafts (ctas/observer.js, Framework v2 Track 4) against their schemas.

Runs scripts/test_ctas_observer.js over a sample of real dossiers of the release in ctas/data
and validates every draft it makes: the Swift ToO request, the TNS AT report (for a record
without a TNS name, and the "fresh" variant of every record), the TNS classification report
and the AstroNote draft, against ctas/schemas/{swift-too-request,tns-at-report,
tns-classification-report,astronote-draft}.schema.json. The schemas follow the TNS bulk-report
JSON and the Swift TOO request field names; nothing is submitted anywhere.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "ctas" / "data"
SCHEMAS = ROOT / "ctas" / "schemas"
SAMPLE = 400


def validator(name: str):
    from jsonschema import Draft202012Validator
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


class ObserverDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("jsonschema is not installed")
        if not (DATA / "candidate-chunks").is_dir():
            raise unittest.SkipTest("no CTAS release under ctas/data (run scripts/overlay_published_data.sh)")
        done = subprocess.run(["node", str(ROOT / "scripts" / "test_ctas_observer.js"), str(DATA), str(SAMPLE)],
                              capture_output=True, text=True, timeout=600, check=True)
        cls.rows = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]

    def test_a_real_sample_is_drafted(self):
        self.assertGreaterEqual(len(self.rows), 50)
        self.assertTrue(all(row["forced"] for row in self.rows), "every record with coordinates gets forced-photometry requests")

    def test_every_draft_validates(self):
        checks = {"swift": validator("swift-too-request"), "at": validator("tns-at-report"), "fresh": validator("tns-at-report"),
                  "classification": validator("tns-classification-report"), "astronote": validator("astronote-draft")}
        problems = []
        for row in self.rows:
            for key, check in checks.items():
                if row.get(key) is None:
                    continue
                for error in check.iter_errors(row[key]):
                    problems.append(f"{row['event_id']} {key}: {'/'.join(map(str, error.absolute_path))}: {error.message}")
        self.assertEqual(problems[:20], [])
        self.assertTrue(any(row["fresh"] for row in self.rows), "the AT report variant is drafted")

    def test_gap_ids_are_from_the_stated_rule(self):
        allowed = {"classification-spectrum", "counterpart-search", "redshift", "second-band", "recent-point", "host-context"}
        for row in self.rows:
            self.assertLessEqual(set(row["gaps"]), allowed, row["event_id"])

    def test_the_schemas_reject_a_broken_draft(self):
        broken = {"at_report": {"0": {"ra": {"value": "1", "units": "furlongs"}}}}
        self.assertTrue(list(validator("tns-at-report").iter_errors(broken)))
        self.assertTrue(list(validator("swift-too-request").iter_errors({"source_name": "x", "urgency": 9})))


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
