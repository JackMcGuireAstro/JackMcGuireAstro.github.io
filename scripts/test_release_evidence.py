#!/usr/bin/env python3
"""The release evidence kept in the repository is well formed and still true (Track 7).

  - benchmarks/: every file parses and names its schema; the identity results are within
    their own thresholds; the page budgets keep the framework's targets;
  - benchmarks/science/fixtures.json: when the releases are present (deploy), each fixture
    record is still in the release and its stable facts (classification labels, retraction
    kept, identity state, status, controversial listing) are unchanged; a change means the
    benchmark must be regenerated (python3 scripts/build_benchmarks.py) or the change is a
    finding;
  - repro/*/MANIFEST.json: every listed file exists with that SHA-256;
  - review/ and release/: every relative link resolves; KNOWN_LIMITATIONS.md covers both
    products.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks"
STABLE = {"ctas": ("classification_labels", "timeline_mentions_retraction"),
          "worldsindex": ("identity_state", "normalized_status", "in_controversial_retracted_excluded_view")}


def load_builder():
    spec = importlib.util.spec_from_file_location("build_benchmarks", ROOT / "scripts/build_benchmarks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BenchmarkFileTests(unittest.TestCase):
    def test_every_benchmark_file_parses_and_names_its_schema(self):
        files = sorted(BENCH.rglob("*.json"))
        self.assertGreaterEqual(len(files), 7)
        for path in files:
            document = json.loads(path.read_text())
            self.assertTrue(str(document.get("schema", "")).strip(), path)

    def test_identity_results_are_within_their_thresholds(self):
        doc = json.loads((BENCH / "identity/worldsindex-gold-set.json").read_text())
        limits = doc["thresholds"]
        for result in doc["results"]:
            ok = result["false_merges"] <= limits["false_merges_max"] and result["false_splits"] <= limits["false_splits_max"]
            self.assertEqual(ok, result["within_thresholds"], result["atlas"])

    def test_budgets_keep_the_framework_targets(self):
        doc = json.loads((BENCH / "performance/budgets.json").read_text())
        self.assertEqual(doc["targets"], {"lcp_ms": 2500, "js_gzip_bytes": 200000})
        self.assertEqual(set(doc["pages"]), {"home", "ctas", "worldsindex", "tonight", "tools"})

    def test_restore_drill_report_says_what_ran(self):
        reports = sorted((BENCH / "recovery").glob("drill-*.json"))
        self.assertTrue(reports)
        latest = json.loads(reports[-1].read_text())
        for product in ("ctas", "worldsindex"):
            entry = latest["products"][product]
            self.assertTrue("ok" in entry or entry.get("state") == "not-run", product)


class ScienceFixtureTests(unittest.TestCase):
    def setUp(self):
        if not (ROOT / "ctas/data/candidate-chunks/manifest.json").exists() or not (ROOT / "worldsindex/data/manifest.json").exists():
            self.skipTest("the published releases are not overlaid in this tree")
        self.builder = load_builder()
        self.recorded = {f.get("role"): f for f in json.loads((BENCH / "science/fixtures.json").read_text())["fixtures"]}

    def test_fixture_records_and_their_stable_facts_still_hold(self):
        current = [self.builder.ctas_fixture(ROOT, role, name) for role, name in self.builder.CTAS_FIXTURES.items()]
        current += [self.builder.wi_fixture(ROOT, role, oid) for role, oid in self.builder.WI_FIXTURES.items()]
        for fixture in current:
            role = fixture["role"]
            self.assertNotEqual(fixture.get("state"), "not-in-this-release", f"{role}: the fixture record is no longer published")
            product = "ctas" if role in self.builder.CTAS_FIXTURES else "worldsindex"
            for key in STABLE[product]:
                self.assertEqual(fixture["facts"][key], self.recorded[role]["facts"][key],
                                 f"{role}: {key} changed; regenerate benchmarks/ or treat as a finding")


class BundleAndPacketTests(unittest.TestCase):
    def test_reproducibility_manifests_match_their_files(self):
        for product in ("ctas", "worldsindex"):
            folder = ROOT / "repro" / product
            manifest = json.loads((folder / "MANIFEST.json").read_text())["files"]
            for name in ("README.md", "reproduce.ipynb", "environment/requirements.txt", "analysis/config.json", "methods.md"):
                self.assertIn(name, manifest, f"{product}: {name}")
            for name, digest in manifest.items():
                self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(), digest, f"{product}/{name}")
            notebook = json.loads((folder / "reproduce.ipynb").read_text())
            self.assertTrue(any(c["cell_type"] == "code" for c in notebook["cells"]))
            self.assertTrue(all(not c.get("outputs") for c in notebook["cells"] if c["cell_type"] == "code"), "notebooks are kept without outputs")

    def test_relative_links_in_the_packet_resolve(self):
        for folder in ("review", "release", "benchmarks"):
            for page in (ROOT / folder).glob("*.md"):
                for target in re.findall(r"`(\.\./[^`\s]+|[a-z][\w-]*\.md)`", page.read_text()):
                    path = (page.parent / target).resolve()
                    self.assertTrue(path.exists(), f"{page.relative_to(ROOT)} -> {target}")

    def test_known_limitations_cover_both_products(self):
        text = (ROOT / "KNOWN_LIMITATIONS.md").read_text()
        for heading in ("## Both products", "## CTAS", "## WorldsIndex"):
            self.assertIn(heading, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
