#!/usr/bin/env python3
"""Release-check classification, watch mode and promotion (with schema digests), the
fallback decision, the deploy's candidate choice, the data checks, the one fallback attempt
of the cloud runners (temporary git repositories, fake publishers), and the wiring of both
publishers, their runners and the deploy (RESILIENCE.md)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_checks as rc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
CLASSES = rc.load_classes()
NOW = dt.datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
MAIN, GOOD, OTHER = "a" * 40, "b" * 40, "c" * 40
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def at(text):
    return rc.parse_time(text)


def result(check_id, outcome, scope="ctas-publish", **extra):
    entry = CLASSES["checks"].get(check_id, {})
    return {"id": check_id, "scope": scope, "outcome": outcome, "class": extra.pop("cls", entry.get("class", "integrity")),
            "check_class": entry.get("class", "integrity"), "mode": "block",
            "fallback_eligible": entry.get("fallback", True) and bool(entry), "parts": [], "output_tail": [], **extra}


def promoted(*ids, scope="ctas-publish"):
    return {"scope": scope, "checks": {cid: {"first_seen": "2026-10-01T00:00:00Z", "consecutive_passes": 9,
                                             "promoted_at": "2026-10-02T00:00:00Z"} for cid in ids}}


def evaluate(check_id, exit_code, output="", history=None, scope="ctas-publish", now=NOW, **kwargs):
    return rc.evaluate(check_id, scope, exit_code, output, CLASSES, history, now, **kwargs)


UNITTEST_311 = """test_a (__main__.CertificateAndArtifactTests.test_a) ... ok
======================================================================
ERROR: test_real_rights_cleared_regression_records_remain_inspectable (__main__.CertificateAndArtifactTests.test_real_rights_cleared_regression_records_remain_inspectable)
----------------------------------------------------------------------
KeyError: 'AT2026zxx'
"""
UNITTEST_310 = "FAIL: test_columnar_index_is_lossless (__main__.CertificateAndArtifactTests)\n"
UNITTEST_SETUP = "ERROR: setUpClass (__main__.CertificateAndArtifactTests)\n"
UNITTEST_SUBTEST = ("FAIL: test_public_copy_calls_it_snapshot_verification_not_certification "
                    "(__main__.CertificateAndArtifactTests.test_public_copy_calls_it_snapshot_verification_not_certification) (path='ctas.html')\n")
CTAS_SCHEMAS = """ok    metaschema (JSON Schema 2020-12)          26 schema(s)
ok    status.schema.json                        1 file(s)
FAIL  candidate-chunk.schema.json             257 file(s)
      ctas/data/candidate-chunks/000.json::$.candidates[3]: 'links' is a required property
ok    certification.schema.json                 1 file(s)
skip  delivery.schema.json                 delivery.json is not present
1 schema check(s) failed after 20.1 s
"""
CTAS_STATUS_FAIL = "FAIL  status.schema.json                        1 file(s)\n      ::$.sources: bad\n"
WI_SCHEMAS = """schemas: 30 top-level JSON files validated fully in 4.0s; 40 of 256 detail shards sampled
FAIL  published JSON does not match worldsindex/schemas:
  source-monitor.schema.json: Schema validation errors were encountered.
    ::$.observations[2].changed: None is not of type 'boolean'
"""
WI_UNDECLARED = """FAIL  published JSON does not match worldsindex/schemas:
  lightcurves/extra.json.gz: published but not declared by the manifest
"""


# ============================================================================ the registry
class ClassesFileTests(unittest.TestCase):
    def test_the_file_is_valid_and_every_check_has_a_class_and_a_scope(self):
        self.assertEqual(rc.classes_problems(CLASSES), [])
        self.assertEqual(CLASSES["schema"], "check-classes@2")
        self.assertTrue(CLASSES["rule"].startswith("Classify a new check by what its failure proves"))
        for check_id, entry in CLASSES["checks"].items():
            self.assertIn(entry["class"], rc.CLASSES, check_id)
            self.assertTrue(set(entry["scopes"]) <= {"ctas-publish", "worldsindex-publish", "deploy"}, check_id)
            self.assertTrue(entry.get("what"), check_id)
        self.assertEqual(CLASSES["watch"]["hours"], 24)
        self.assertEqual(CLASSES["watch"]["consecutive_passes"], {"ctas-publish": 4, "worldsindex-publish": 3, "deploy": 3})

    def test_problems_are_reported(self):
        bad = {"schema": "check-classes@1", "watch": {"consecutive_passes": {"x": 0}},
               "checks": {"Bad Id": {"class": "nope", "scopes": []},
                          "x.y": {"class": "integrity", "scopes": ["deploy"], "since": "2026-10-20", "baseline": False,
                                  "parts": [["(", "advisory"]]}}}
        problems = " | ".join(rc.classes_problems(bad))
        for needle in ("schema must be check-classes@2", "consecutive_passes must map", "Bad Id: id must be",
                       "class must be one of", "'since' is gone", "baseline is either true or absent", "need a known parser"):
            self.assertIn(needle, problems)

    def test_always_block_is_exactly_the_hard_core(self):
        always = sorted(cid for cid, entry in CLASSES["checks"].items() if entry["class"] == "always_block")
        self.assertEqual(always, ["ctas.checksums", "ctas.gate.public-export-safety", "ctas.release-layout",
                                  "site.credential-scan", "worldsindex.artifact-guard", "worldsindex.checksums",
                                  "worldsindex.credential-scan"])
        # secrets never fall back to older code; the undeclared-file part of the WorldsIndex
        # schema validator is always_block too
        for cid in ("ctas.gate.public-export-safety", "site.credential-scan", "worldsindex.credential-scan"):
            self.assertIs(CLASSES["checks"][cid]["fallback"], False, cid)
        self.assertIn(["published but not declared by the manifest", "always_block"], CLASSES["checks"]["worldsindex.schemas"]["parts"])

    def test_baseline_marks_the_checks_that_ran_before_the_history(self):
        new = sorted(cid for cid, entry in CLASSES["checks"].items() if not entry.get("baseline"))
        for cid in ("ctas.schemas", "worldsindex.schemas", "ctas.test_ctas_interop", "worldsindex.test_worldsindex_schemas",
                    "ctas.gate.interoperability-exports-round-trip", "ctas.count-drop", "worldsindex.count-drop",
                    "ctas.checksums", "worldsindex.checksums"):
            self.assertIn(cid, new)
        for cid in ("ctas.test_ctas_static", "ctas.export", "worldsindex.test_worldsindex_static", "ctas.gate.catalog-population"):
            self.assertTrue(CLASSES["checks"][cid].get("baseline"), cid)

    def test_the_outage_checks_are_classified_as_the_batch_intends(self):
        checks = CLASSES["checks"]
        for cid in ("ctas.test_ctas_browser", "ctas.gate.bootstrap-performance-budget", "ctas.gate.catalog-page-budget",
                    "ctas.test_ctas_interop", "ctas.test_ctas_changelog", "ctas.test_ctas_feed",
                    "worldsindex.test_worldsindex_interop", "worldsindex.test_worldsindex_interop_js",
                    "ctas.gate.interoperability-exports-round-trip"):
            self.assertEqual(checks[cid]["class"], "advisory", cid)
        for name in ("tonight", "tools", "research", "worldsindex_lightcurves", "worldsindex_live_sources", "worldsindex_sky"):
            self.assertEqual(checks[f"browser.{name}"]["class"], "advisory")
        for cid in ("ctas.test_ctas_static", "ctas.gate.catalog-population", "ctas.count-drop", "worldsindex.count-drop",
                    "ctas.gate.source-and-survey-closure", "ctas.test_ctas_ingest_provenance"):
            self.assertEqual(checks[cid]["class"], "integrity", cid)
        self.assertIs(checks["worldsindex.restore"]["fallback"], False)
        self.assertIs(checks["worldsindex.exonexus"]["fallback"], False)

    def test_every_certificate_gate_is_classified(self):
        source = (ROOT / "scripts/export_ctas_snapshot.py").read_text()
        constants = dict(re.findall(r'^([A-Z_]+_GATE_ID) = "([a-z0-9-]+)"', source, re.MULTILINE))
        block = source[source.index("    gates = [\n"):source.index('    clock.done("verification-gates")')]
        ids = re.findall(r'gate\(\s*"([a-z0-9-]+)"', block) + [constants[name] for name in re.findall(r"gate\(([A-Z_]+_GATE_ID)", block)]
        self.assertEqual(len(set(ids)), 44)
        for gate in ids:
            self.assertIn(f"ctas.gate.{gate}", CLASSES["checks"], gate)
        certificate = ROOT / "ctas/data/certification.json"
        if certificate.is_file():   # the overlaid real release, when present
            for gate in json.loads(certificate.read_text())["gates"]:
                self.assertIn(f"ctas.gate.{gate['id']}", CLASSES["checks"], gate["id"])


# ============================================================================ wiring
class WiringTests(unittest.TestCase):
    """Every id the scripts run is classified for that scope, and every classified id is run."""

    @staticmethod
    def ids_in(text):
        found = set(re.findall(r"^\s*check ([a-z0-9][a-z0-9_.-]+) ", text, re.MULTILINE))
        found |= set(re.findall(r"^\s*(?:if .*?)?record_check ([a-z0-9][a-z0-9_.-]+) ", text, re.MULTILINE))
        found |= set(re.findall(r"ACTIVE_CHECK=([a-z0-9][a-z0-9_.-]+)", text))
        found |= set(re.findall(r"--id ([a-z0-9][a-z0-9_.-]+)", text))
        return found

    def test_publishers_runners_and_deploy_use_only_classified_ids(self):
        scopes = {
            "ctas-publish": ["scripts/publish_ctas.sh", "scripts/ctas_cloud_run.sh"],
            "worldsindex-publish": ["scripts/publish_worldsindex.sh", "scripts/worldsindex_cloud_run.sh"],
            "deploy": ["scripts/deploy_checks.sh"],
        }
        used = {}
        for scope, files in scopes.items():
            ids = set()
            for name in files:
                ids |= self.ids_in((ROOT / name).read_text())
            used[scope] = ids
        publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        loop = publisher[publisher.index("for suite in scripts/test_ctas_static.py"):publisher.index("; do", publisher.index("for suite in scripts/test_ctas_static.py"))]
        used["ctas-publish"] |= {"ctas." + Path(path).stem for path in re.findall(r"scripts/(test_ctas_\w+)\.py", loop)}
        deploy = (ROOT / "scripts/deploy_checks.sh").read_text()
        tooling = deploy[deploy.index("group_tooling()"):deploy.index("group_content()")]
        used["deploy"] |= {"ops." + name for name in re.findall(r"\b([a-z_]+)\b", tooling[tooling.index("for name in"):tooling.index("; do")]) if name not in ("for", "name", "in")}
        used["ctas-publish"] |= {cid for cid in CLASSES["checks"] if cid.startswith("ctas.gate.")}
        for scope, ids in used.items():
            for check_id in ids:
                with self.subTest(scope=scope, check=check_id):
                    self.assertIn(check_id, CLASSES["checks"])
                    self.assertIn(scope, CLASSES["checks"][check_id]["scopes"])
        for check_id, entry in CLASSES["checks"].items():
            for scope in entry["scopes"]:
                with self.subTest(check=check_id, scope=scope):
                    self.assertIn(check_id, used[scope], f"{check_id} is classified for {scope} but never run there")

    def test_every_deploy_test_file_of_the_old_workflow_is_still_run(self):
        deploy = (ROOT / "scripts/deploy_checks.sh").read_text()
        for name in ("test_worldsindex_static.py", "test_worldsindex_science.mjs", "test_worldsindex_photometry.mjs",
                     "test_worldsindex_publisher_recovery.py", "test_worldsindex_live_mast.mjs", "test_worldsindex_live_nasa.mjs",
                     "test_worldsindex_live_sources.mjs", "test_worldsindex_ephemerides.mjs", "test_worldsindex_interop.mjs",
                     "test_worldsindex_front.mjs", "test_worldsindex_interop.py", "test_worldsindex_schemas.py",
                     "validate_worldsindex_schemas.py", "test_qualquest_static.py", "test_tonight_planner.mjs",
                     "test_tools_coordinates.mjs", "test_tools_exposure.mjs", "test_tools_finder.mjs", "test_research_census.mjs",
                     "test_ctas_outcomes.py", "test_ctas_chunks.py", "test_ctas_links.py", "test_ctas_publisher_recovery.py",
                     "test_ctas_static.py", "test_ctas_positional.py", "test_ctas_source_health.py", "test_ctas_feed.py",
                     "test_ctas_chunk_loader.js", "test_ctas_presentation.js", "test_ctas_live_sources.js", "test_live_relay.mjs",
                     "test_ctas_spectrum_ascii.js", "test_ctas_spectrum_fits.js", "test_ctas_lightcurve_summary.js",
                     "test_ctas_followup.mjs", "test_ctas_interop.py", "test_ctas_changelog.py", "test_ctas_sky.js",
                     "validate_ctas_schemas.py", "test_ctas_astro_evidence.py", "test_ctas_browser.py", "test_tonight_browser.py",
                     "test_tools_browser.py", "test_research_browser.py", "test_worldsindex_lightcurves_browser.py",
                     "test_worldsindex_live_sources_browser.py", "test_worldsindex_sky_browser.py",
                     "ctas/followup.js", "ctas/sky.js", "ctas/changelog.js", "ctas/status.js", "worldsindex/assets/app.js",
                     "tonight/planner.js", "research/barium-dwarfs/census.js"):
            self.assertIn(name, deploy, name)
        for name in ("watchdog_freshness", "cloud_run_marker", "publisher_housekeeping", "ctas_db_retention", "data_branch",
                     "ctas_cloud_state", "worldsindex_cloud_state", "release_commit"):
            self.assertIn(name, deploy)
        self.assertNotIn("ctas_release_code", deploy)
        self.assertNotIn("CTAS_CODE_ROOT", (ROOT / "scripts/test_ctas_static.py").read_text())

    def test_the_deploy_workflow_validates_candidates_in_order(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/worldsindex-release.yml").read_text())
        self.assertEqual(workflow["permissions"]["deployments"], "read")
        validate = workflow["jobs"]["validate"]["steps"]
        runs = "\n".join(step.get("run", "") for step in validate)
        for group in ("worldsindex-static", "worldsindex-interop", "qualquest", "tonight", "ctas-static", "ctas-schemas",
                      "browser", "tooling", "content"):
            self.assertIn(f"bash scripts/deploy_checks.sh {group} .", runs)
        names = [step.get("name") for step in validate]
        self.assertIn("CTAS static release", names)
        self.assertIn("CTAS page in a real browser, and evidence schema", names)
        choose = next(step for step in validate if step.get("id") == "choose")
        self.assertIn("bash scripts/deploy_candidates.sh", choose["run"])
        self.assertEqual(choose["env"]["CTAS_DATA_COMMIT"], "${{ steps.overlay.outputs.ctas_commit }}")
        upload = next(step for step in validate if step.get("uses", "").startswith("actions/upload-pages-artifact"))
        self.assertEqual(upload["with"]["path"], "${{ steps.choose.outputs.tree }}")
        header = (ROOT / ".github/workflows/worldsindex-release.yml").read_text().split("\non:")[0]
        self.assertIn("every deployed site's CTAS-bound code is byte-identical to the", header)
        candidates = (ROOT / "scripts/deploy_candidates.sh").read_text()
        for needed in ('candidates=("main=$MAIN=$CHECK_RESULTS")', '"ctas-release=$CTAS_RELEASE" "last-deployment=$LAST_DEPLOY"',
                       "release_commit.py\" read --product ctas", "release_commit.py\" last-deployment",
                       "release_commit.py\" on-main", "CHECK_SKIP_MISSING=1 CHECK_ONLY_BLOCKING=1",
                       'git -C "$SITE" fetch --quiet --no-tags --depth=1 origin "$commit"', 'worktree add --quiet --detach "$tree" "$commit"',
                       'git -C "$SITE" archive "$CTAS_DATA_COMMIT" -- ctas/data', 'LANDING_WINDOW_HOURS:-3'):
            self.assertIn(needed, candidates)
        record = workflow["jobs"]["record"]
        self.assertEqual(record["if"], "always()")
        record_runs = "\n".join(step.get("run", "") for step in record["steps"])
        self.assertNotIn("site-last-good", record_runs)
        self.assertIn("scripts/release_issues.py sync --scope deploy", record_runs)
        self.assertIn("scripts/run_report.py push --report", record_runs)
        self.assertIn("history/deploy.json", record_runs)

    def test_a_hanging_browser_suite_cannot_block_through_the_step_time_limit(self):
        # The browser step may hit its timeout-minutes (a page waiting for "networkidle" on a
        # slow third-party host): continue-on-error keeps the deploy going. Its only blocking
        # check runs first and under its own `timeout`, so it is recorded, never missing.
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/worldsindex-release.yml").read_text())
        step = next(s for s in workflow["jobs"]["validate"]["steps"] if s.get("run", "").strip() == "bash scripts/deploy_checks.sh browser .")
        self.assertIs(step.get("continue-on-error"), True)
        deploy = (ROOT / "scripts/deploy_checks.sh").read_text()
        body = deploy.split("group_browser() {", 1)[1].split("\n}", 1)[0]
        ids = re.findall(r"^\s*check (\S+) (.*)$", body, re.MULTILINE)
        self.assertEqual(ids[0][0], "ctas.test_ctas_astro_evidence")
        self.assertTrue(ids[0][1].startswith("timeout "), ids[0][1])
        for check_id, _ in ids[1:]:
            self.assertEqual(CLASSES["checks"][check_id]["class"], "advisory", check_id)
        # timeout-minutes of the step bounds the group: the blocking check's own limit is inside it
        seconds = int(re.search(r"timeout -k \d+ (\d+) ", ids[0][1]).group(1))
        self.assertLess(seconds + 10, step["timeout-minutes"] * 60)

    def test_cloud_workflows_may_write_issues(self):
        for name in ("ctas-cloud.yml", "worldsindex-cloud.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            self.assertIn("issues: write", text)
            self.assertIn('RELEASE_ISSUE_MENTION: "@JackMcGuireAstro"', text)

    def test_publishers_classify_and_keep_their_contracts(self):
        ctas = (ROOT / "scripts/publish_ctas.sh").read_text()
        worlds = (ROOT / "scripts/publish_worldsindex.sh").read_text()
        for text in (ctas, worlds):
            self.assertIn('. "$SITE/scripts/release_checks.sh"', text)
            self.assertIn("block() { say \"FAIL  $*\"; exit 3; }", text)
        self.assertIn('CHECK_RESULTS="${CTAS_CHECK_RESULTS:-$CHECK_DIR/results.jsonl}"', ctas)
        self.assertIn('PUBLIC_FILES+=(ctas/data/check-history.json)', ctas)
        self.assertIn('show "$DATA_TIP:ctas/data/check-history.json"', ctas)
        self.assertIn('if [ -n "${CTAS_FROZEN_DB:-}" ]; then', ctas)
        self.assertIn('"${CTAS_KEEP_FROZEN_DB:-}"', ctas)
        self.assertIn("check ctas.checksums python3 scripts/release_checks.py checksums --product ctas", ctas)
        self.assertIn("check ctas.count-drop python3 scripts/release_checks.py count-drop --product ctas", ctas)
        self.assertLess(ctas.index("check ctas.checksums"), ctas.index("release_checks.py gates"))
        self.assertLess(ctas.index("release_checks.py gates"), ctas.index("release_checks.py history"))
        self.assertLess(ctas.index("release_checks.py history"), ctas.index('if [ "$DRY" -eq 1 ]; then'))
        self.assertIn('PUBLIC_FILES+=(worldsindex/data/check-history.json)', worlds)
        self.assertIn('CHECK_TREE="$SITE"', worlds)
        self.assertIn("check worldsindex.checksums in_site python3 scripts/release_checks.py checksums --product worldsindex", worlds)
        self.assertIn("check worldsindex.count-drop", worlds)
        self.assertLess(worlds.index("# ---- Generic artifact guard"), worlds.index("PUBLIC_FILES+=(worldsindex/data/check-history.json)"))
        self.assertLess(worlds.index("release_checks.py\" history"), worlds.index('if [ "$DRY" -eq 1 ]; then'))
        for runner, product in (("ctas_cloud_run.sh", "ctas"), ("worldsindex_cloud_run.sh", "worldsindex")):
            text = (ROOT / "scripts" / runner).read_text()
            self.assertIn('. "$SITE/scripts/code_fallback.sh"', text)
            self.assertEqual(text.count("code_fallback_run "), 1, runner)          # at most one attempt per run
            self.assertIn(f'release_commit_of "$SITE" {product}', text)
            self.assertIn("publish_with() {", text)
            self.assertIn("scripts/release_issues.py\" sync", text)
            self.assertIn("scripts/run_report.py\" push --report", text)
            self.assertIn('--site-commit "$MAIN_COMMIT"', text)
            self.assertNotIn("last_good", text)
            # the state is saved after the fallback attempt, exactly as before
            self.assertLess(text.index("code_fallback_run "), text.index("_cloud_state.py\" save"))
        self.assertIn('CTAS_FROZEN_DB="$FROZEN"', (ROOT / "scripts/ctas_cloud_run.sh").read_text())
        self.assertIn("local args=(--fast --force)", (ROOT / "scripts/worldsindex_cloud_run.sh").read_text())
        for script in ("publish_ctas.sh", "publish_worldsindex.sh", "ctas_cloud_run.sh", "worldsindex_cloud_run.sh",
                       "deploy_checks.sh", "deploy_candidates.sh", "release_checks.sh", "code_fallback.sh"):
            self.assertEqual(subprocess.run(["/bin/bash", "-n", str(ROOT / "scripts" / script)]).returncode, 0, script)

    def test_new_files_are_bound_or_declared_unbound_with_a_reason(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("exporter_for_binding", ROOT / "scripts/export_ctas_snapshot.py")
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        bound, unbound = set(exporter.BOUND_CODE_PATHS), exporter.UNBOUND_RESILIENCE_PATHS
        self.assertFalse(bound & set(unbound))
        for path in ("scripts/release_checks.py", "scripts/release_checks.sh", "scripts/check-classes.json",
                     "scripts/run_report.py", "ctas/schemas/check-history.schema.json"):
            self.assertIn(path, bound)
        for path in ("scripts/release_issues.py", "scripts/code_fallback.sh", "scripts/release_commit.py", "scripts/deploy_checks.sh",
                     "scripts/deploy_candidates.sh", "scripts/test_release_checks.py", "scripts/test_run_report.py",
                     "scripts/test_release_issues.py", "scripts/test_release_commit.py", "RESILIENCE.md"):
            self.assertIn(path, unbound)
            self.assertTrue(unbound[path].strip())
        for path in list(bound) + [p for p in unbound if not p.startswith(".github")]:
            self.assertTrue((ROOT / path).exists(), path)


# ============================================================================ parsers and classes
class ParserTests(unittest.TestCase):
    def test_unittest_headers_in_both_python_forms(self):
        self.assertEqual(rc.parse_unittest(UNITTEST_311),
                         ["CertificateAndArtifactTests.test_real_rights_cleared_regression_records_remain_inspectable"])
        self.assertEqual(rc.parse_unittest(UNITTEST_310), ["CertificateAndArtifactTests.test_columnar_index_is_lossless"])
        self.assertEqual(rc.parse_unittest(UNITTEST_SETUP), ["CertificateAndArtifactTests.setUpClass"])
        self.assertEqual(rc.parse_unittest(UNITTEST_SUBTEST * 2),
                         ["CertificateAndArtifactTests.test_public_copy_calls_it_snapshot_verification_not_certification"])
        self.assertEqual(rc.parse_unittest("Traceback (most recent call last):\nImportError: x\n"), [])

    def test_schema_validator_lines(self):
        self.assertEqual(rc.parse_ctas_schemas(CTAS_SCHEMAS), ["candidate-chunk.schema.json 257 file(s)"])
        self.assertEqual(rc.parse_skipped_schemas(CTAS_SCHEMAS), ["delivery.schema.json"])
        self.assertEqual(rc.parse_worldsindex_schemas(WI_SCHEMAS), ["source-monitor.schema.json: Schema validation errors were encountered."])
        self.assertEqual(rc.parse_worldsindex_schemas("FAIL  schema feed.schema.json is missing\n"), ["schema feed.schema.json is missing"])
        entry = CLASSES["checks"]["worldsindex.schemas"]
        self.assertEqual(rc.schema_of(entry, "details/0a.json.gz: bad"), "details-shard.schema.json")
        self.assertEqual(rc.schema_of(entry, "manifest.schema.json: bad"), "manifest.schema.json")
        self.assertIsNone(rc.schema_of(entry, "extra.json: published but not declared by the manifest"))

    def test_schema_digests_include_the_referenced_schemas(self):
        with tempfile.TemporaryDirectory(prefix="digests-") as folder:
            folder = Path(folder)
            (folder / "common.schema.json").write_text('{"$defs": {"x": {"type": "string"}}}')
            (folder / "a.schema.json").write_text('{"properties": {"x": {"$ref": "common.schema.json#/$defs/x"}}}')
            (folder / "b.schema.json").write_text('{"type": "object"}')
            before = rc.schema_digests(folder)
            (folder / "common.schema.json").write_text('{"$defs": {"x": {"type": "integer"}}}')
            after = rc.schema_digests(folder)
            self.assertNotEqual(before["a.schema.json"], after["a.schema.json"])
            self.assertNotEqual(before["common.schema.json"], after["common.schema.json"])
            self.assertEqual(before["b.schema.json"], after["b.schema.json"])


class ClassificationTests(unittest.TestCase):
    def test_suite_parts_classify_one_by_one(self):
        static = evaluate("ctas.test_ctas_static", 1, UNITTEST_311)[0]
        self.assertEqual((static["class"], static["outcome"]), ("advisory", "warn"))
        both = evaluate("ctas.test_ctas_static", 1, UNITTEST_311 + UNITTEST_310)[0]
        self.assertEqual((both["class"], both["outcome"]), ("integrity", "block"))
        self.assertEqual(evaluate("ctas.test_ctas_static", 1, UNITTEST_SETUP)[0]["outcome"], "block")
        self.assertEqual(evaluate("ctas.test_ctas_static", 1, "ImportError: boom")[0]["outcome"], "block")
        unknown = evaluate("ctas.mystery", 1, "boom")[0]
        self.assertEqual((unknown["outcome"], unknown["fallback_eligible"]), ("block", False))

    def test_the_schema_validators_split_into_one_check_per_schema_file(self):
        rows = evaluate("ctas.schemas", 1, CTAS_SCHEMAS, promoted("ctas.schemas"))
        family, subs = rows[0], {row["id"].split(":", 1)[1]: row for row in rows[1:]}
        self.assertEqual(len(subs), len(list((ROOT / "ctas/schemas").glob("*.schema.json"))))
        self.assertEqual(subs["candidate-chunk.schema.json"]["outcome"], "warn")      # advisory schema
        self.assertEqual(subs["candidate-chunk.schema.json"]["class"], "advisory")
        self.assertEqual(subs["status.schema.json"]["outcome"], "pass")
        self.assertEqual(subs["delivery.schema.json"]["outcome"], "skip")
        self.assertRegex(subs["status.schema.json"]["schema_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual((family["outcome"], family["class"]), ("warn", "advisory"))
        # status.schema.json is integrity: blocks once ITS sub-check is promoted, warns before
        new_status = evaluate("ctas.schemas", 1, CTAS_STATUS_FAIL, promoted("ctas.schemas"))
        self.assertEqual((new_status[0]["outcome"], new_status[0]["class"]), ("warn", "integrity"))
        digest = rc.schema_digests(ROOT / "ctas/schemas")["status.schema.json"]
        history = promoted("ctas.schemas", "ctas.schemas:status.schema.json")
        history["checks"]["ctas.schemas:status.schema.json"]["sha256"] = digest
        blocked = evaluate("ctas.schemas", 1, CTAS_STATUS_FAIL, history)
        self.assertEqual((blocked[0]["outcome"], blocked[0]["class"]), ("block", "integrity"))
        self.assertIn("status.schema.json", blocked[0]["reason"])
        # a crash names no schema: the family's own class and watch mode decide
        self.assertEqual(evaluate("ctas.schemas", 1, "error: check-jsonschema is not installed", promoted("ctas.schemas"))[0]["outcome"], "block")
        self.assertEqual(evaluate("ctas.schemas", 1, "error: check-jsonschema is not installed", {})[0]["outcome"], "warn")
        # all pass: the family and every schema file pass (their counts advance)
        passed = evaluate("ctas.schemas", 0, "ok")
        self.assertEqual({row["outcome"] for row in passed}, {"pass"})

    def test_an_undeclared_file_always_blocks_even_in_watch_mode(self):
        rows = evaluate("worldsindex.schemas", 1, WI_UNDECLARED, {}, scope="worldsindex-publish")
        self.assertEqual((rows[0]["outcome"], rows[0]["class"]), ("block", "always_block"))
        # not attributed to lightcurve.schema.json, whose class or watch period could soften it
        self.assertTrue(all(row["outcome"] == "pass" for row in rows[1:]))
        monitor = evaluate("worldsindex.schemas", 1, WI_SCHEMAS, promoted("worldsindex.schemas", scope="worldsindex-publish"),
                           scope="worldsindex-publish")
        self.assertEqual(monitor[0]["outcome"], "warn")

    def test_secrets_never_reach_a_result(self):
        row = evaluate("ctas.export", 1, "token ghp_abcdefghijklmnopqrstuvwxyz0123 and s3cretvalue", secret_values=["s3cretvalue"])[0]
        text = json.dumps(row)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz0123", text)
        self.assertNotIn("s3cretvalue", text)
        tail = evaluate("ctas.export", 1, "\n".join(f"line {i}" for i in range(200)))[0]["output_tail"]
        self.assertEqual((len(tail), tail[-1]), (60, "line 199"))


# ============================================================================ watch mode
class WatchModeTests(unittest.TestCase):
    def mode(self, check_id, history, now=NOW, scope="ctas-publish", cls=None, sha=None):
        entry = CLASSES["checks"].get(check_id.split(":", 1)[0])
        hours, needed = rc.watch_settings(CLASSES, scope)
        return rc.mode_for(check_id, cls or (entry or {}).get("class", "integrity"), entry, history, now, hours, needed, sha)["mode"]

    def test_classes_and_bootstrap(self):
        self.assertEqual(self.mode("ctas.test_ctas_browser", None), "warn")              # advisory
        self.assertEqual(self.mode("ctas.checksums", None), "block")                     # always_block, never watched
        self.assertEqual(self.mode("ctas.not-a-check", None), "block")                   # unclassified
        self.assertEqual(self.mode("ctas.export", None), "block")                        # baseline, no history yet
        self.assertEqual(self.mode("ctas.schemas", None), "watch")                       # new, no history yet
        self.assertEqual(self.mode("ctas.count-drop", None), "watch")
        # once a history exists, a baseline id it does not know is a NEW check: watch
        some = promoted("ctas.export")
        self.assertEqual(self.mode("ctas.test_ctas_static", some), "watch")
        # the bootstrap is per owning scope: the publishers' histories do not make the deploy's
        # baseline checks new
        merged = rc.merge_histories([promoted("ctas.export"), promoted("worldsindex.static-build", scope="worldsindex-publish")], CLASSES)
        self.assertEqual(self.mode("ctas.front-syntax", merged, scope="deploy"), "block")
        self.assertEqual(self.mode("ctas.front-syntax", rc.merge_histories([merged, promoted("x.y", scope="deploy")], CLASSES), scope="deploy"), "watch")

    def test_promotion_needs_24_hours_and_n_consecutive_passes_per_product(self):
        def history(passes, first="2026-10-22T11:00:00Z", scope="ctas-publish", cid="ctas.schemas"):
            return {"scope": scope, "checks": {cid: {"first_seen": first, "consecutive_passes": passes, "promoted_at": None}}}
        self.assertEqual(self.mode("ctas.schemas", history(3)), "watch")                 # CTAS needs 4
        self.assertEqual(self.mode("ctas.schemas", history(4)), "block")
        self.assertEqual(self.mode("ctas.schemas", history(9, first="2026-10-23T00:00:00Z")), "watch")   # only 12 h
        wi = history(3, scope="worldsindex-publish", cid="worldsindex.schemas")
        self.assertEqual(self.mode("worldsindex.schemas", wi, scope="worldsindex-publish"), "block")    # WorldsIndex needs 3
        wi["checks"]["worldsindex.schemas"]["consecutive_passes"] = 2
        self.assertEqual(self.mode("worldsindex.schemas", wi, scope="worldsindex-publish"), "watch")

    def test_history_counts_promotes_resets_and_is_permanent(self):
        scope = "ctas-publish"
        first = dt.datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
        history = rc.update_history(None, [result("ctas.schemas", "pass")], CLASSES, scope, first, MAIN)
        self.assertEqual((history["schema"], history["scope"], history["site_commit"]), ("check-history@1", scope, MAIN))
        self.assertEqual(history["watch"], {"hours": 24.0, "consecutive_passes": 4})
        row = history["checks"]["ctas.schemas"]
        self.assertEqual((row["first_seen"], row["consecutive_passes"], row["last_result"], row["promoted_at"]),
                         ("2026-10-22T10:00:00Z", 1, "pass", None))
        failing = result("ctas.schemas", "warn", parts=[{"part": "candidate-chunk.schema.json 257 file(s)", "class": "advisory"}])
        history = rc.update_history(history, [failing], CLASSES, scope, first + dt.timedelta(hours=1))
        row = history["checks"]["ctas.schemas"]
        self.assertEqual((row["consecutive_passes"], row["last_result"], row["last_failure_detail"]),
                         (0, "fail", "candidate-chunk.schema.json 257 file(s)"))
        history = rc.update_history(history, [result("ctas.schemas", "skip")], CLASSES, scope, first + dt.timedelta(hours=2))
        self.assertEqual(history["checks"]["ctas.schemas"]["consecutive_passes"], 0)
        for hour in (3, 4, 5, 6):
            history = rc.update_history(history, [result("ctas.schemas", "pass")], CLASSES, scope, first + dt.timedelta(hours=hour))
        self.assertIsNone(history["checks"]["ctas.schemas"]["promoted_at"])               # 4 passes but < 24 h
        history = rc.update_history(history, [result("ctas.schemas", "pass")], CLASSES, scope, first + dt.timedelta(hours=25))
        self.assertEqual(history["checks"]["ctas.schemas"]["promoted_at"], "2026-10-23T11:00:00Z")
        self.assertEqual(self.mode("ctas.schemas", history, now=first + dt.timedelta(hours=26)), "block")
        # promotion is permanent: a later failure resets the count, not the promotion
        history = rc.update_history(history, [result("ctas.schemas", "block")], CLASSES, scope, first + dt.timedelta(hours=27))
        self.assertEqual((history["checks"]["ctas.schemas"]["consecutive_passes"], history["checks"]["ctas.schemas"]["promoted_at"]),
                         (0, "2026-10-23T11:00:00Z"))
        self.assertEqual(self.mode("ctas.schemas", history, now=first + dt.timedelta(hours=28)), "block")

    def test_bootstrap_seeds_baseline_checks_and_rows_of_other_code_are_carried(self):
        history = rc.update_history({}, [result("ctas.export", "pass"), result("ctas.count-drop", "pass")], CLASSES, "ctas-publish", NOW)
        self.assertEqual(history["checks"]["ctas.export"]["promoted_at"], "2026-10-23T12:00:00Z")
        self.assertIn("baseline", history["checks"]["ctas.export"]["seeded"])
        self.assertIsNone(history["checks"]["ctas.count-drop"]["promoted_at"])
        # a row of a check this code does not run (newer code's) is carried, then pruned after 30 days
        history["checks"]["ctas.future-check"] = {"first_seen": "2026-10-23T00:00:00Z", "consecutive_passes": 2,
                                                   "promoted_at": None, "last_seen": "2026-10-23T00:00:00Z"}
        carried = rc.update_history(history, [result("ctas.export", "pass")], CLASSES, "ctas-publish", NOW + dt.timedelta(days=1))
        self.assertEqual(carried["checks"]["ctas.future-check"]["consecutive_passes"], 2)
        pruned = rc.update_history(carried, [result("ctas.export", "pass")], CLASSES, "ctas-publish", NOW + dt.timedelta(days=31))
        self.assertNotIn("ctas.future-check", pruned["checks"])
        # results of another scope are not this scope's history
        other = rc.update_history({}, [result("worldsindex.schemas", "pass", scope="worldsindex-publish")], CLASSES, "ctas-publish", NOW)
        self.assertEqual(other["checks"], {})

    def test_a_changed_schema_file_starts_a_new_watch_period(self):
        scope = "ctas-publish"
        sub = "ctas.schemas:candidate-chunk.schema.json"
        rows = [dict(result(sub, "pass"), family="ctas.schemas", schema_sha256="1" * 64)]
        history = {"scope": scope, "checks": {sub: {"first_seen": "2026-10-01T00:00:00Z", "consecutive_passes": 9,
                                                    "promoted_at": "2026-10-02T00:00:00Z", "sha256": "1" * 64}}}
        self.assertEqual(self.mode(sub, history, cls="integrity", sha="1" * 64), "block")
        self.assertEqual(self.mode(sub, history, cls="integrity", sha="2" * 64), "watch")
        changed = rc.update_history(history, [dict(rows[0], schema_sha256="2" * 64)], CLASSES, scope, NOW)
        row = changed["checks"][sub]
        self.assertEqual((row["sha256"], row["first_seen"], row["consecutive_passes"], row["promoted_at"]),
                         ("2" * 64, "2026-10-23T12:00:00Z", 1, None))
        same = rc.update_history(history, rows, CLASSES, scope, NOW)
        self.assertEqual(same["checks"][sub]["consecutive_passes"], 10)
        # end to end: the real CTAS schemas with a history recorded for a different digest
        stale = promoted("ctas.schemas", "ctas.schemas:status.schema.json")
        stale["checks"]["ctas.schemas:status.schema.json"]["sha256"] = "0" * 64
        self.assertEqual(evaluate("ctas.schemas", 1, CTAS_STATUS_FAIL, stale)[0]["outcome"], "warn")

    def test_histories_merge_by_owner(self):
        ctas = {"scope": "ctas-publish", "checks": {"ctas.schemas": {"first_seen": "2026-10-20T00:00:00Z", "consecutive_passes": 2, "promoted_at": None}}}
        deploy = {"scope": "deploy", "checks": {"ctas.schemas": {"first_seen": "2026-10-20T00:00:00Z", "consecutive_passes": 9, "promoted_at": None}}}
        for order in ([deploy, ctas], [ctas, deploy]):
            merged = rc.merge_histories(order, CLASSES)
            self.assertEqual(merged["checks"]["ctas.schemas"]["consecutive_passes"], 2)
            self.assertEqual(sorted(merged["scopes_read"]), ["ctas-publish", "deploy"])

    def test_check_history_schemas_accept_what_the_engine_writes(self):
        try:
            import jsonschema
            from referencing import Registry, Resource
        except ImportError:
            self.skipTest("jsonschema not installed")
        rows = evaluate("ctas.schemas", 1, CTAS_SCHEMAS) + [result("ctas.export", "pass")]
        history = rc.update_history({}, rows, CLASSES, "ctas-publish", NOW, MAIN)
        self.assertIn("ctas.schemas:candidate-chunk.schema.json", history["checks"])
        common = json.loads((ROOT / "ctas/schemas/common.schema.json").read_text())
        registry = Registry().with_resource("common.schema.json", Resource.from_contents(common))
        for product, scope in (("ctas", "ctas-publish"), ("worldsindex", "worldsindex-publish")):
            schema = json.loads((ROOT / f"{product}/schemas/check-history.schema.json").read_text())
            document = history if product == "ctas" else rc.update_history(
                {}, evaluate("worldsindex.schemas", 1, WI_SCHEMAS, scope=scope), CLASSES, scope, NOW)
            jsonschema.Draft202012Validator(schema, registry=registry).validate(document)
        self.assertLess(len(json.dumps(history)), 30000)


# ============================================================================ the decision
class DecisionTests(unittest.TestCase):
    def decide(self, main, **extra):
        defaults = {"main_commit": MAIN, "now": NOW, "release_commit": GOOD}
        return rc.decide(main, **{**defaults, **extra})

    def test_proceed_and_errors_outside_checks(self):
        self.assertEqual(self.decide([result("ctas.export", "pass")])["action"], "proceed")
        warned = self.decide([result("ctas.test_ctas_browser", "warn")])
        self.assertEqual((warned["action"], warned["warnings"]), ("proceed", ["ctas.test_ctas_browser"]))
        self.assertEqual(self.decide([], main_exit=1)["reason"], "error-outside-checks")
        self.assertEqual(self.decide([], main_exit=0)["action"], "proceed")
        # a blocking schema sub-check is reported through its family only
        family = [result("ctas.schemas", "warn"), dict(result("ctas.schemas:x.schema.json", "block"), family="ctas.schemas")]
        self.assertEqual(self.decide(family)["action"], "proceed")

    def test_when_no_fallback_applies(self):
        blocked = [result("ctas.export", "block", output_tail=["KeyError: 'links'"])]
        self.assertEqual(self.decide([result("ctas.gate.public-export-safety", "block")])["reason"], "not-fallback-eligible")
        self.assertEqual(self.decide([result("worldsindex.restore", "block")])["reason"], "not-fallback-eligible")
        self.assertEqual(self.decide(blocked, release_commit=None)["reason"], "no-release-commit")
        self.assertEqual(self.decide(blocked, release_commit=MAIN)["reason"], "release-commit-is-current")
        self.assertEqual(self.decide(blocked, release_commit_on_main=False)["reason"], "release-commit-not-on-main")
        self.assertEqual(self.decide(blocked, fallback_check_ids={"ctas.schemas"})["reason"], "new-check-not-in-fallback-code")
        tried = self.decide(blocked, fallback_check_ids={"ctas.export"}, release_commit_on_main=True)
        self.assertEqual((tried["action"], tried["blocking_detail"]), ("try-fallback", "KeyError: 'links'"))
        self.assertEqual(self.decide(blocked)["action"], "try-fallback")       # on-main unknown: allowed

    def test_after_the_fallback_run(self):
        blocked = [result("ctas.export", "block")]
        used = self.decide(blocked, fallback_results=[result("ctas.export", "pass")], fallback_exit=0)
        self.assertEqual((used["action"], used["fallback"]), ("use-fallback", True))
        self.assertEqual(rc.code_fallback_record(used), {"failed_commit": MAIN, "failed_check": "ctas.export",
                                                         "failed_checks": ["ctas.export"], "detail": "exit None", "used_commit": GOOD})
        same = self.decide(blocked, fallback_results=[result("ctas.export", "block")], fallback_exit=3)
        self.assertEqual((same["action"], same["reason"]), ("block", "data-integrity"))
        other = self.decide(blocked, fallback_results=[result("ctas.export", "pass"), result("ctas.test_ctas_links", "block")], fallback_exit=3)
        self.assertEqual(other["reason"], "fallback-failed")
        error = self.decide(blocked, fallback_results=[result("ctas.export", "pass")], fallback_exit=1)
        self.assertEqual(error["reason"], "fallback-error")
        # code older than the classification records no results; its exit code says it all
        self.assertEqual(self.decide(blocked, fallback_results=[], fallback_exit=0)["action"], "use-fallback")
        self.assertIsNone(rc.code_fallback_record(error))

    def test_the_five_outages_replayed_through_the_classification_and_the_decision(self):
        when = at("2026-10-21T12:00:00Z")
        schemas = promoted("ctas.schemas", "worldsindex.schemas")
        # 2026-10-18: the restore refused hard links: integrity, never falls back -> blocked, issue at once
        restore = evaluate("worldsindex.restore", 1, "refusing hard link member", scope="worldsindex-publish", now=when)
        self.assertEqual(self.decide(restore, now=when)["reason"], "not-fallback-eligible")
        # 2026-10-19: test pinned AT2026zxx by name -> advisory part -> the release proceeds
        static = evaluate("ctas.test_ctas_static", 1, UNITTEST_311, now=when)
        self.assertEqual((static[0]["outcome"], static[0]["class"]), ("warn", "advisory"))
        self.assertEqual(self.decide(static, now=when)["action"], "proceed")
        # 2026-10-21: 'links' required in the dossier schema -> an advisory schema file, even when promoted
        links = evaluate("ctas.schemas", 1, CTAS_SCHEMAS, schemas, now=when)
        self.assertEqual(self.decide(links, now=when)["action"], "proceed")
        # 2026-10-21: deploy browser tests (overflow, first-screen budget) -> advisory -> deploy proceeds
        browser = evaluate("ctas.test_ctas_browser", 1, "FAIL: test_status_page_fits", scope="deploy", now=when)
        self.assertEqual(self.decide(browser, now=when)["action"], "proceed")
        # 2026-10-21: monitor quarantine 'changed: null' -> advisory schema file -> publish proceeds
        monitor = evaluate("worldsindex.schemas", 1, WI_SCHEMAS, promoted("worldsindex.schemas", scope="worldsindex-publish"),
                           scope="worldsindex-publish", now=when)
        self.assertEqual(self.decide(monitor, now=when)["action"], "proceed")
        # a brand-new schema that is integrity class (say a new manifest schema) only warns in its first day
        fresh = evaluate("ctas.schemas", 1, CTAS_STATUS_FAIL, {}, now=when)
        self.assertEqual(self.decide(fresh, now=when)["action"], "proceed")
        # a fresh exporter change that crashes on real data -> blocked, retried with the release's code
        crash = evaluate("ctas.export", 1, "KeyError: 'links'", now=when)
        self.assertEqual(self.decide(crash, now=when)["action"], "try-fallback")


class ChooseTests(unittest.TestCase):
    """The deploy's choice among candidate site codes (a pure function over their results)."""

    def candidates(self, main=None, release=None, deployment=None, release_commit=GOOD, deployment_commit=OTHER):
        return [{"label": "main", "commit": MAIN, "results": main},
                {"label": "ctas-release", "commit": release_commit, "results": release},
                {"label": "last-deployment", "commit": deployment_commit, "results": deployment}]

    def test_main_first_and_advisory_failures_never_block(self):
        choice = rc.choose(self.candidates(main=[result("ctas.export", "pass"), result("browser.tools", "warn", scope="deploy")]))
        self.assertEqual((choice["chosen"]["label"], choice["fallback"]), ("main", False))
        self.assertEqual(choice["chosen"]["warnings"], ["browser.tools"])
        self.assertEqual(len(choice["tried"]), 1)

    def test_the_first_candidate_whose_blocking_checks_pass(self):
        main = [result("ctas.test_ctas_static", "block", scope="deploy")]
        choice = rc.choose(self.candidates(main=main, release=[result("ctas.test_ctas_static", "pass", scope="deploy")]))
        self.assertEqual((choice["chosen"]["label"], choice["chosen"]["commit"], choice["fallback"]), ("ctas-release", GOOD, True))
        self.assertEqual(choice["main_blocking"], ["ctas.test_ctas_static"])
        third = rc.choose(self.candidates(main=main, release=main, deployment=[result("ctas.export", "pass")]))
        self.assertEqual(third["chosen"]["label"], "last-deployment")
        none = rc.choose(self.candidates(main=main, release=main, deployment=main))
        self.assertIsNone(none["chosen"])
        self.assertEqual([row["status"] for row in none["tried"]], ["blocked", "blocked", "blocked"])

    def test_duplicates_unavailable_and_unvalidated_candidates_are_skipped(self):
        main = [result("ctas.export", "block")]
        same = rc.choose(self.candidates(main=main, release=None, deployment=[result("ctas.export", "pass")], deployment_commit=MAIN))
        self.assertEqual([row["status"] for row in same["tried"]], ["blocked", "not-validated", "duplicate"])
        self.assertIsNone(same["chosen"])
        missing = rc.choose(self.candidates(main=main, release_commit=None, deployment=[result("ctas.export", "pass")]))
        self.assertEqual([row["status"] for row in missing["tried"]], ["blocked", "unavailable", "passed"])
        empty = rc.choose(self.candidates(main=main, release=[]))
        self.assertEqual(empty["tried"][1]["status"], "blocked")       # no check ran: never deployed blind
        many = rc.choose(self.candidates(main=main, release=main, deployment=main)
                         + [{"label": "extra", "commit": "d" * 40, "results": [result("ctas.export", "pass")]}])
        self.assertEqual(many["tried"][-1]["status"], "not-tried")
        self.assertIsNone(many["chosen"])


# ============================================================================ data checks
class DataCheckTests(unittest.TestCase):
    def test_checksum_self_consistency(self):
        with tempfile.TemporaryDirectory(prefix="checksums-") as folder:
            root = Path(folder)
            (root / "ctas/data/candidate-chunks").mkdir(parents=True)
            (root / "worldsindex/data/details").mkdir(parents=True)
            files = {"ctas/data/catalog-index.json": b'{"a": 1}', "ctas/data/candidate-chunks/000.json": b"[]"}
            for path, raw in files.items():
                (root / path).write_bytes(raw)
            certificate = {"files": {path: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for path, raw in files.items()}}
            certificate["files"]["scripts/export_ctas_snapshot.py"] = {"sha256": "0" * 64, "bytes": 1}    # code: not this check's
            (root / "ctas/data/certification.json").write_text(json.dumps(certificate))
            self.assertEqual(rc.checksum_problems("ctas", root), ([], 2))
            (root / "ctas/data/candidate-chunks/000.json").write_bytes(b"[1]")
            problems, _ = rc.checksum_problems("ctas", root)
            self.assertEqual(problems, ["ctas/data/candidate-chunks/000.json: 3 bytes, the release states 2"])
            (root / "ctas/data/candidate-chunks/000.json").write_bytes(b"{}")
            self.assertIn("sha256 differs", rc.checksum_problems("ctas", root)[0][0])
            (root / "ctas/data/candidate-chunks/000.json").unlink()
            self.assertIn("listed but missing", rc.checksum_problems("ctas", root)[0][0])
            (root / "worldsindex/data/details/00.json.gz").write_bytes(b"x")
            manifest = {"artifacts": {"details/00.json.gz": {"sha256": hashlib.sha256(b"x").hexdigest(), "bytes": 1}}}
            (root / "worldsindex/data/manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(rc.checksum_problems("worldsindex", root), ([], 1))
            cli = subprocess.run([sys.executable, str(ROOT / "scripts/release_checks.py"), "checksums", "--product", "ctas",
                                  "--root", str(root)], capture_output=True, text=True)
            self.assertEqual(cli.returncode, 1)
            self.assertIn("FAIL", cli.stdout)

    def test_count_drop(self):
        self.assertIsNone(rc.count_drop_problem("ctas", {"candidate_count": 8000}, {"candidate_count": 10000})[0])   # -20%
        self.assertIsNotNone(rc.count_drop_problem("ctas", {"candidate_count": 7999}, {"candidate_count": 10000})[0])
        self.assertIsNone(rc.count_drop_problem("ctas", {"candidate_count": 12000}, {"candidate_count": 10000})[0])
        self.assertIsNone(rc.count_drop_problem("worldsindex", {"objectCount": 31337}, None)[0])
        self.assertIsNotNone(rc.count_drop_problem("worldsindex", {"objectCount": 100}, {"objectCount": 31337})[0])
        self.assertIsNotNone(rc.count_drop_problem("worldsindex", {}, {"objectCount": 31337})[0])


# ============================================================================ command line
class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(ROOT / "scripts/release_checks.py"), *args], text=True, capture_output=True,
                              env={**os.environ, "GITHUB_ACTIONS": "", **(env or {})})

    def test_record_gates_history_decide_choose_and_summary(self):
        with tempfile.TemporaryDirectory(prefix="release-checks-") as folder:
            folder = Path(folder)
            results = folder / "results.jsonl"
            (folder / "out.txt").write_text(UNITTEST_311)
            warn = self.run_cli("record", "--scope", "ctas-publish", "--id", "ctas.test_ctas_static", "--exit-code", "1",
                                "--output", str(folder / "out.txt"), "--results", str(results), "--now", "2026-10-23T12:00:00Z")
            self.assertEqual(warn.returncode, 0, warn.stderr)
            self.assertIn("WARN  check ctas.test_ctas_static failed", warn.stdout)
            block = self.run_cli("record", "--scope", "ctas-publish", "--id", "ctas.export", "--exit-code", "1",
                                 "--message", "Traceback", "--results", str(results))
            self.assertEqual(block.returncode, 3)
            self.assertIn("FAIL  check ctas.export failed", block.stdout)
            (folder / "schemas.txt").write_text(CTAS_SCHEMAS)
            schemas = self.run_cli("record", "--scope", "ctas-publish", "--id", "ctas.schemas", "--exit-code", "1",
                                   "--output", str(folder / "schemas.txt"), "--results", str(results), "--tree", str(ROOT))
            self.assertEqual(schemas.returncode, 0, schemas.stdout + schemas.stderr)
            self.assertIn("WARN  check ctas.schemas:candidate-chunk.schema.json failed", schemas.stdout)
            certificate = {"gates": [{"id": "catalog-population", "passed": True, "evidence": "ok"},
                                     {"id": "bootstrap-performance-budget", "passed": False, "evidence": "1.2 MB > 1.1 MB"}]}
            (folder / "certification.json").write_text(json.dumps(certificate))
            gates = self.run_cli("gates", "--certificate", str(folder / "certification.json"), "--results", str(folder / "gates.jsonl"))
            self.assertEqual(gates.returncode, 0, gates.stdout + gates.stderr)
            self.assertIn("1/2 passed", gates.stdout)
            certificate["gates"][0]["passed"] = False
            (folder / "certification.json").write_text(json.dumps(certificate))
            self.assertEqual(self.run_cli("gates", "--certificate", str(folder / "certification.json"),
                                          "--results", str(folder / "gates2.jsonl")).returncode, 3)
            history = self.run_cli("history", "--scope", "ctas-publish", "--results", str(results),
                                   "--previous", str(folder / "missing.json"), "--out", str(folder / "history.json"))
            self.assertEqual(history.returncode, 0, history.stderr)
            document = json.loads((folder / "history.json").read_text())
            self.assertEqual(document["checks"]["ctas.schemas:candidate-chunk.schema.json"]["last_result"], "fail")
            self.assertEqual(document["checks"]["ctas.schemas:status.schema.json"]["consecutive_passes"], 1)
            self.assertIn("still in watch mode", history.stdout)
            output = folder / "github-output"
            decided = self.run_cli("decide", "--results", str(results), "--main-commit", MAIN, "--release-commit", GOOD,
                                   "--release-commit-on-main", "yes", "--out", str(folder / "decision.json"), "--github-output", str(output))
            self.assertEqual(decided.stdout.strip(), "try-fallback")
            self.assertIn("action=try-fallback", output.read_text())
            self.assertEqual(json.loads((folder / "decision.json").read_text())["blocking"], ["ctas.export"])
            # fallback code older than the classification knows the baseline checks only
            (folder / "fallback.jsonl").write_text("")
            old = self.run_cli("decide", "--results", str(results), "--main-commit", MAIN, "--release-commit", GOOD,
                               "--fallback-classes", "/dev/null", "--fallback-results", str(folder / "fallback.jsonl"),
                               "--fallback-exit", "0", "--out", str(folder / "decision2.json"))
            self.assertEqual(old.stdout.strip(), "use-fallback")
            self.assertEqual(json.loads((folder / "decision2.json").read_text())["code_fallback"]["used_commit"], GOOD)
            (folder / "pass.jsonl").write_text(json.dumps(result("ctas.export", "pass")) + "\n")
            chosen = self.run_cli("choose", "--candidate", f"main={MAIN}={results}", "--candidate", f"ctas-release={GOOD}={folder / 'pass.jsonl'}",
                                  "--candidate", f"last-deployment={OTHER}=", "--out", str(folder / "choice.json"))
            self.assertEqual(chosen.stdout.strip(), f"ctas-release {GOOD}")
            summary = self.run_cli("summary", "--results", str(results))
            self.assertIn("2 warning(s), 1 blocking", summary.stdout)
            self.assertEqual(self.run_cli("blocking", "--id", "browser.tools").returncode, 1)
            self.assertEqual(self.run_cli("blocking", "--id", "ctas.schemas").returncode, 0)
            self.assertEqual(self.run_cli("blocking", "--id", "ctas.unknown").returncode, 0)

    def test_the_bash_check_function(self):
        with tempfile.TemporaryDirectory(prefix="release-checks-sh-") as folder:
            script = f"""
set -uo pipefail
RELEASE_CHECKS={ROOT / 'scripts/release_checks.py'}
CHECK_SCOPE=deploy
CHECK_RESULTS={folder}/results.jsonl
CHECK_DIR={folder}/out
. {ROOT / 'scripts/release_checks.sh'}
check ctas.test_ctas_browser false; echo "warn=$?"
check site.credential-scan false; echo "block=$?"
check ctas.test_ctas_static true; echo "pass=$?"
CHECK_SKIP_MISSING=1 check ctas.test_ctas_outcomes python3 scripts/no_such_test.py; echo "skip=$?"
CHECK_ONLY_BLOCKING=1 check browser.tools false; echo "advisory-skipped=$?"
CHECK_ONLY_BLOCKING=1 check ctas.front-syntax false; echo "integrity-run=$?"
RELEASE_CHECKS=/nonexistent/release_checks.py check ctas.front.sky true; echo "broken=$?"
"""
            done = subprocess.run(["/bin/bash", "-c", script], cwd=ROOT, text=True, capture_output=True)
            for needle in ("warn=0", "block=3", "pass=0", "skip=0", "advisory-skipped=0", "integrity-run=3", "broken=3"):
                self.assertIn(needle, done.stdout, done.stdout + done.stderr)
            rows = [json.loads(line) for line in Path(folder, "results.jsonl").read_text().splitlines()]
            self.assertEqual([row["outcome"] for row in rows], ["warn", "block", "pass", "skip", "skip", "block"])


# ============================================================================ the one fallback attempt
FAKE_PUBLISHER = r'''#!/bin/bash
# A stand-in publisher: VERSION decides whether its release check blocks.
set -uo pipefail
VERSION="__VERSION__"
SITE=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$PUB_LOG"
echo "$VERSION $(git -C "$SITE" rev-parse HEAD) $FAKE_DATA_TAG $(cat "$FAKE_MARKER_PATH" 2>/dev/null | tr -d '\n ')" >> "$FAKE_RECORD"
if [ "$VERSION" = good ]; then
  python3 "$RC" record --scope ctas-publish --id ctas.export --exit-code 0 --results "$PUB_RESULTS" >/dev/null
  echo "published deadbeef"
  exit 0
fi
python3 "$RC" record --scope ctas-publish --id ctas.export --exit-code 1 --message "KeyError: 'links'" --results "$PUB_RESULTS"
echo "FAIL  export failed; nothing committed"
exit 3
'''


class FallbackAttemptTests(unittest.TestCase):
    """scripts/code_fallback.sh against temporary git repositories: an origin with a good and a
    bad commit, a data branch whose release names the good commit, a shallow site clone of the
    bad commit, and fake publishers. The GitHub CLI is a stub."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="code-fallback-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        origin = self.tmp / "origin"
        self.git("init", "-q", "-b", "main", str(origin))
        (origin / "scripts").mkdir()
        for name in ("release_checks.py", "run_report.py", "check-classes.json", "release_commit.py", "cloud_run_marker.py", "code_fallback.sh"):
            shutil.copy(ROOT / "scripts" / name, origin / "scripts" / name)
        self.write_publisher(origin, "good")
        self.git("add", ".", cwd=origin)
        self.git("commit", "-qm", "good code", cwd=origin)
        self.good = self.git("rev-parse", "HEAD", cwd=origin)
        self.write_publisher(origin, "bad")
        self.git("commit", "-qam", "bad code", cwd=origin)
        self.bad = self.git("rev-parse", "HEAD", cwd=origin)
        # the data branch: the published release says the good commit produced it
        self.git("checkout", "-q", "--orphan", "ctas-data", cwd=origin)
        self.git("rm", "-rqf", ".", cwd=origin)
        (origin / "ctas/data").mkdir(parents=True)
        (origin / "ctas/data/certification.json").write_text(json.dumps({"publication_relationship": {"local_head": self.good}}))
        self.git("add", "ctas", cwd=origin)
        self.git("commit", "-qm", "release", cwd=origin)
        self.git("checkout", "-q", "main", cwd=origin)
        self.site = self.tmp / "site"
        self.git("clone", "-q", "--depth=1", "--no-single-branch", f"file://{origin}", str(self.site))
        self.git("checkout", "-q", "main", cwd=self.site)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text("#!/bin/bash\necho \"$*\" >> \"$GH_CALLS\"\necho \"{\\\"status\\\": \\\"$FAKE_COMPARE\\\"}\"\n")
        gh.chmod(0o755)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True, env=GIT_ENV).stdout.strip()

    def write_publisher(self, repo, version):
        path = repo / "scripts/fake_publish.sh"
        path.write_text(FAKE_PUBLISHER.replace("__VERSION__", version))
        path.chmod(0o755)

    def run_attempt(self, status_main=None, compare="ahead", release_commit=None):
        work = self.tmp / "work"
        shutil.rmtree(work, ignore_errors=True)
        (work / "logs").mkdir(parents=True)
        (work / "cloud-run.json").write_text(json.dumps({"schema": "cloud-run-marker@1", "token_status": "ok", "site_commit": self.bad}))
        script = f'''
set -uo pipefail
SITE={self.site}; WORK={work}; LOGS={work}/logs
say() {{ printf '%s\\n' "$*" | tee -a "$LOGS/runner.log"; }}
. "$SITE/scripts/code_fallback.sh"
MAIN_COMMIT=$(git -C "$SITE" rev-parse HEAD)
CHECKS_MAIN=$WORK/checks-main.jsonl; CHECKS_FALLBACK=$WORK/checks-fallback.jsonl; DECISION=$WORK/decision.json
MARKER=$WORK/cloud-run.json; FALLBACK_SCOPE=ctas-publish; FALLBACK_WORKFLOW=ctas-cloud.yml
export RC="$SITE/scripts/release_checks.py" FAKE_RECORD=$WORK/record.txt FAKE_DATA_TAG=frozen-db-1
publish_with() {{ PUB_LOG="$2" PUB_RESULTS="$3" FAKE_MARKER_PATH="$4" bash "$1/scripts/fake_publish.sh"; }}
OUT=$(PUB_LOG=$LOGS/publish PUB_RESULTS=$CHECKS_MAIN FAKE_MARKER_PATH=$MARKER bash "$SITE/scripts/fake_publish.sh"); STATUS=$?
{f'STATUS={status_main}' if status_main is not None else ''}
RELEASE_COMMIT=${{FORCE_RELEASE_COMMIT:-$(release_commit_of "$SITE" ctas)}}
code_fallback_run ctas "$STATUS"
echo "RESULT action=$ACTION used=$FALLBACK_USED status=${{FALLBACK_STATUS:-none}} release=$RELEASE_COMMIT"
'''
        env = {**GIT_ENV, "PATH": f"{self.bin}:{os.environ['PATH']}", "GH_CALLS": str(self.tmp / "gh-calls.txt"),
               "FAKE_COMPARE": compare, "GITHUB_REPOSITORY": "o/r", "DEPLOY_TOKEN": "t0ken-not-real"}
        if release_commit:
            env["FORCE_RELEASE_COMMIT"] = release_commit
        done = subprocess.run(["/bin/bash", "-c", script], text=True, capture_output=True, env=env)
        result_line = next((line for line in done.stdout.splitlines() if line.startswith("RESULT ")), "")
        record = (work / "record.txt").read_text().splitlines() if (work / "record.txt").exists() else []
        decision = json.loads((work / "decision.json").read_text()) if (work / "decision.json").exists() else {}
        return result_line, record, decision, done

    def test_the_release_commit_publishes_once_when_the_current_code_is_blocked(self):
        line, record, decision, done = self.run_attempt()
        self.assertIn(f"action=use-fallback used=1 status=0 release={self.good}", line, done.stdout + done.stderr)
        self.assertEqual(len(record), 2)                               # the bad run, then exactly one fallback run
        self.assertTrue(record[0].startswith(f"bad {self.bad} frozen-db-1"))
        self.assertTrue(record[1].startswith(f"good {self.good} frozen-db-1"))   # same data, the good commit's code
        self.assertIn('"code_fallback"', record[1])
        self.assertIn(f'"used_commit":"{self.good}"', record[1])
        self.assertIn('"token_status":"ok"', record[1])                 # the run's own marker, extended
        self.assertEqual(decision["code_fallback"]["failed_check"], "ctas.export")
        self.assertEqual(decision["code_fallback"]["failed_commit"], self.bad)
        self.assertIn(f"compare/{self.good}...{self.bad}", (self.tmp / "gh-calls.txt").read_text())
        self.assertFalse((self.tmp / "work/fallback-site").exists())    # cleaned up

    def test_no_fallback_when_it_must_not_or_cannot_help(self):
        line, record, decision, _ = self.run_attempt(compare="diverged")
        self.assertIn("action=block used=0 status=none", line)
        self.assertEqual(decision["reason"], "release-commit-not-on-main")
        self.assertEqual(len(record), 1)
        line, record, decision, _ = self.run_attempt(release_commit=self.bad)   # the release was built by this code
        self.assertEqual((decision["reason"], len(record)), ("release-commit-is-current", 1))
        line, record, decision, _ = self.run_attempt(status_main=1)            # failure outside the checks
        self.assertIn("used=0", line)
        self.assertEqual(len(record), 1)

    def test_the_same_failure_with_the_fallback_code_means_the_data_is_bad(self):
        # A third commit on main is the current code; the release was produced by the bad
        # commit, whose publisher fails the same check on the same data.
        origin = self.tmp / "origin"
        self.write_publisher(origin, "bad")
        (origin / "scripts/marker.txt").write_text("another bad one\n")
        self.git("add", ".", cwd=origin)
        self.git("commit", "-qm", "another bad", cwd=origin)
        third = self.git("rev-parse", "HEAD", cwd=origin)
        self.git("fetch", "-q", "--depth=1", "origin", "main", cwd=self.site)
        self.git("checkout", "-q", "--detach", third, cwd=self.site)
        line, record, decision, done = self.run_attempt(release_commit=self.bad)
        self.assertIn("action=block used=0 status=3", line, done.stdout + done.stderr)
        self.assertEqual(decision["reason"], "data-integrity")
        self.assertEqual(len(record), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
