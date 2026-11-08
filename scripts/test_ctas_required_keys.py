#!/usr/bin/env python3
"""Every published CTAS dossier carries every key the dossier schema requires — all of them.

2026-11-08: 285 of 18,677 dossiers (Rubin/LSST diaObject records whose only aliases come from
providers outside the exporter's PUBLIC_LINKS) had no `designations` key, which
ctas/schemas/candidate-chunk.schema.json requires. The deploy's schema check validates a
one-in-sixteen sample of chunks and classes dossier-shape failures as presentation (advisory),
so the gap showed only as an intermittent warning. The exporter now always writes the list
(empty when there is no public designation) and its candidate-public-contract gate checks every
dossier; this test checks every chunk root and part of the published release the same way.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
CHUNKS = ROOT / "ctas" / "data" / "candidate-chunks"
SCHEMA = ROOT / "ctas" / "schemas" / "candidate-chunk.schema.json"


class RequiredKeysTests(unittest.TestCase):
    def test_the_exporter_reads_the_required_keys_from_the_schema(self):
        import export_ctas_snapshot as exporter
        required = exporter.dossier_required_keys(ROOT)
        self.assertEqual(required, set(json.loads(SCHEMA.read_text())["$defs"]["candidate"]["required"]))
        self.assertIn("designations", required)

    def test_the_exporter_always_writes_designations(self):
        source = (ROOT / "scripts" / "export_ctas_snapshot.py").read_text()
        self.assertIn('        rec["designations"] = [', source)  # outside `if links:`, so an empty list when none
        self.assertIn("dossiers carry all {len(schema_required)} schema-required keys", source)

    @unittest.skipUnless((CHUNKS / "manifest.json").exists(), "the CTAS release is not overlaid (bash scripts/overlay_published_data.sh)")
    def test_every_published_dossier_has_every_required_key(self):
        required = set(json.loads(SCHEMA.read_text())["$defs"]["candidate"]["required"])
        missing, total, files = {}, 0, 0
        for path in sorted(CHUNKS.rglob("*.json")):
            if path.name == "manifest.json":
                continue
            document = json.loads(path.read_text())
            if not isinstance(document, dict):
                continue
            files += 1
            for candidate in document.get("candidates", []):
                total += 1
                gap = required - set(candidate)
                if gap:
                    missing[str(candidate.get("name") or candidate.get("event_id"))] = sorted(gap)
        self.assertGreater(total, 0)
        self.assertEqual(missing, {}, f"{len(missing)} of {total} dossiers in {files} chunk files lack required keys, e.g. "
                         + "; ".join(f"{k}: {v}" for k, v in list(missing.items())[:5]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
