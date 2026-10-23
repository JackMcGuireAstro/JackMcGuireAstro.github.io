#!/usr/bin/env python3
"""Real-shape regression tests for the WorldsIndex release schemas.

The Track 1 schemas were written against healthy test releases. On 2026-10-21 at
15:35 UTC ExoFOP began answering HTTP 503 and exoplanet.eu stopped answering; the
monitor quarantined the attempt (as designed: the last good catalog is kept and the
typed failure receipt is published), and a failed probe's "changed" is null because the
question cannot be answered. source-monitor.schema.json required a boolean, so the
release-time schema check refused every WorldsIndex release for more than a day.

tests/fixtures/worldsindex/source-monitor-quarantined.json is the real public monitor
status of 2026-10-22, trimmed to three observations, with that outage applied exactly
as the builder writes it (packages/exonexus/sync/source-monitor.ts: changed: null).
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "worldsindex" / "schemas"
FIXTURE = ROOT / "tests" / "fixtures" / "worldsindex" / "source-monitor-quarantined.json"


def validator_for(name: str):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    registry = Registry()
    for path in SCHEMAS.glob("*.schema.json"):
        contents = json.loads(path.read_text())
        resource = Resource.from_contents(contents, default_specification=DRAFT202012)
        registry = registry.with_resource(path.resolve().as_uri(), resource)
        if "$id" in contents:
            registry = registry.with_resource(contents["$id"], resource)
    schema = json.loads((SCHEMAS / name).read_text())
    return Draft202012Validator(schema, registry=registry)


class QuarantinedMonitorTests(unittest.TestCase):
    def setUp(self):
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            self.skipTest("jsonschema is not installed")
        self.check = validator_for("source-monitor.schema.json")
        self.document = json.loads(FIXTURE.read_text())

    def errors(self, document):
        return [f"{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in self.check.iter_errors(document)]

    def test_the_fixture_carries_the_outage(self):
        failed = {row["sourceId"]: row for row in self.document["observations"] if row["state"] != "AVAILABLE"}
        self.assertEqual(set(failed), {"exofop", "exoplanet-eu"})
        self.assertTrue(all(row["changed"] is None for row in failed.values()))

    def test_a_quarantined_monitor_status_validates(self):
        self.assertEqual(self.errors(self.document), [])

    def test_changed_is_still_typed(self):
        broken = copy.deepcopy(self.document)
        broken["observations"][0]["changed"] = "yes"
        self.assertTrue(any("changed" in line for line in self.errors(broken)))

    def test_every_nullable_field_of_a_failed_probe_is_nullable_in_the_schema(self):
        # The builder's SourceProbeObservation (packages/exonexus/sync/types.ts) declares these
        # as nullable; a failed probe sets them to null. The schema must accept every one.
        nullable = ("responseSha256", "fingerprint", "recordCount", "latestMarker", "changed",
                    "error", "errorCategory", "httpStatus", "artifact")
        probe = copy.deepcopy(self.document)
        for field in nullable:
            probe["observations"][0][field] = None
        self.assertEqual(self.errors(probe), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
