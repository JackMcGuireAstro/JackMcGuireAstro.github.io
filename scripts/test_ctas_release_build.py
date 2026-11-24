"""The incremental CTAS release (scripts/ctas_release_build.py, 2026-11-24).

Unit tests of the fingerprint encoding, the clock rule, the plan, the dossier store and the
chunk writer; an end-to-end class that releases the fixture database once, then again at a
later clock both in full and incrementally against that first release (through a temporary
git repository, the way the publisher hands the exporter the published data branch), and
requires the two releases to be byte-identical, with a changed row and a fresh discovery
rebuilt and everything else reused.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ctas_release_build as R  # noqa: E402

SPEC = importlib.util.spec_from_file_location("ctas_exporter", ROOT / "scripts/export_ctas_snapshot.py")
X = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(X)
FIXTURE = ROOT / "tests/fixtures/ctas-identity.db.gz"
UTC = timezone.utc


def at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class RowEncodingTests(unittest.TestCase):
    def test_every_value_type_is_distinct_and_stable(self):
        rows = [(None,), (True,), (False,), (0,), (1,), (1.0,), ("1",), (b"1",), ("",), (b"",)]
        encoded = [R.encode_row(b"t", row) for row in rows]
        self.assertEqual(len(set(encoded)), len(rows))
        self.assertEqual(R.encode_row(b"t", ("a", 1)), R.encode_row(b"t", ("a", 1)))
        self.assertNotEqual(R.encode_row(b"t", ("a", 1)), R.encode_row(b"u", ("a", 1)))
        self.assertNotEqual(R.encode_row(b"t", ("ab", "c")), R.encode_row(b"t", ("a", "bc")))

    def test_context_digest_is_canonical(self):
        self.assertEqual(R.context_digest({"b": 1, "a": [1, 2]}), R.context_digest({"a": [1, 2], "b": 1}))
        self.assertNotEqual(R.context_digest({"a": 1}), R.context_digest({"a": 2}))


class ClockRuleTests(unittest.TestCase):
    P = at("2026-11-24T06:00:00Z")
    N = at("2026-11-24T07:00:00Z")

    def test_archival_records_are_stable(self):
        self.assertTrue(R.time_stable(at("2025-01-01T00:00:00Z"), at("2025-01-10T00:00:00Z"), self.P, self.N, 180.0))
        self.assertTrue(R.time_stable(None, None, self.P, self.N, 180.0))

    def test_recent_discovery_and_recent_observation_are_not(self):
        # Recency points fade for 72 h after discovery.
        self.assertFalse(R.time_stable(self.P - timedelta(hours=71), None, self.P, self.N, 180.0))
        self.assertTrue(R.time_stable(self.P - timedelta(hours=73), None, self.P, self.N, 180.0))
        # Observation-gap points grow for 120 h after the latest retained observation.
        self.assertFalse(R.time_stable(at("2026-01-01T00:00:00Z"), self.P - timedelta(hours=119), self.P, self.N, 180.0))
        self.assertTrue(R.time_stable(at("2026-01-01T00:00:00Z"), self.P - timedelta(hours=121), self.P, self.N, 180.0))

    def test_crossing_the_follow_up_window_is_not(self):
        newest = self.P - timedelta(days=180) + timedelta(minutes=30)   # inside at P, outside at N
        self.assertFalse(R.time_stable(newest, None, self.P, self.N, 180.0))
        self.assertTrue(R.time_stable(self.P - timedelta(days=181), None, self.P, self.N, 180.0))
        self.assertTrue(R.time_stable(self.P - timedelta(days=100), None, self.P, self.N, 180.0))

    def test_a_clock_that_went_backwards_is_not(self):
        self.assertFalse(R.time_stable(None, None, self.N, self.P, 180.0))


def facts(ids, fingerprint="a" * 32, **overrides):
    out = {}
    for event_id in ids:
        fact = R.EventFacts(fingerprint=fingerprint)
        for key, value in overrides.items():
            setattr(fact, key, value)
        out[event_id] = fact
    return out


def document(ids, fingerprint="a" * 32, context="ctx", score_as_of="2026-11-24T06:00:00Z", full_at="2026-11-24T06:00:00Z", **build):
    return {
        "schema": R.INPUT_FINGERPRINTS_SCHEMA, "generated_at": score_as_of, "score_as_of": score_as_of,
        "context_digest": context, "statement": "", "build": {"mode": "full", "full_rebuild_at": full_at, **build},
        "fingerprints": {event_id: fingerprint for event_id in ids},
    }


class PlanTests(unittest.TestCase):
    IDS = [f"{index:032x}" for index in range(10)]
    NOW = at("2026-11-24T07:00:00Z")

    def plan(self, **kwargs):
        defaults = dict(enabled=True, as_of=self.NOW, context="ctx", current_ids=self.IDS, facts=facts(self.IDS),
                        discovery_by_id={event_id: at("2025-01-01T00:00:00Z") for event_id in self.IDS},
                        previous_fingerprints=document(self.IDS), full_rebuild_hours=24.0, follow_up_window_days=180.0)
        defaults.update(kwargs)
        return R.plan_release(**defaults)

    def test_full_when_disabled_without_a_previous_document_or_after_a_context_change(self):
        self.assertEqual(self.plan(enabled=False).mode, "full")
        self.assertEqual(self.plan(previous_fingerprints=None).reason, "the previous release carries no input fingerprints")
        self.assertIn("changed", self.plan(context="other").reason)
        self.assertEqual(self.plan(context="other").rebuilt, set(self.IDS))

    def test_scheduled_full_rebuild_and_the_window_after_a_mismatch(self):
        self.assertIn("scheduled", self.plan(previous_fingerprints=document(self.IDS, full_at="2026-11-23T06:59:00Z")).reason)
        self.assertEqual(self.plan(previous_fingerprints=document(self.IDS, full_at="2026-11-23T07:01:00Z")).mode, "incremental")
        self.assertIn("mismatched", self.plan(previous_fingerprints=document(self.IDS, sample_mismatch_at="2026-11-24T01:00:00Z")).reason)
        self.assertEqual(self.plan(previous_fingerprints=document(self.IDS, sample_mismatch_at="2026-11-22T01:00:00Z")).mode, "incremental")

    def test_reuse_decisions(self):
        current = self.IDS + ["f" * 32]                     # one new event
        fact_set = facts(current)
        fact_set[self.IDS[0]].fingerprint = "b" * 32        # changed rows
        fact_set[self.IDS[1]].has_signals = True            # messenger signals: cross-event
        fact_set[self.IDS[2]].has_screens = True
        fact_set[self.IDS[3]].readable = False
        fact_set[self.IDS[4]].latest_observation = self.NOW - timedelta(hours=2)   # recent observation
        discovery = {event_id: at("2025-01-01T00:00:00Z") for event_id in current}
        discovery[self.IDS[5]] = self.NOW - timedelta(hours=10)                   # recent discovery
        plan = self.plan(current_ids=current, facts=fact_set, discovery_by_id=discovery, sample_minimum=2, sample_fraction=0.0)
        self.assertEqual(plan.mode, "incremental")
        self.assertEqual(plan.counts, {"new": 1, "changed": 1, "clock": 2, "cross-event": 2, "unreadable": 1, "unchanged": 4})
        self.assertEqual(plan.reused, set(self.IDS[6:]))
        self.assertEqual(plan.rebuilt, set(current) - plan.reused)
        self.assertEqual(len(plan.sample), 2)
        self.assertTrue(plan.sample <= plan.reused)
        # The sample is seeded by the clock: the same plan picks the same sample.
        again = self.plan(current_ids=current, facts=fact_set, discovery_by_id=discovery, sample_minimum=2, sample_fraction=0.0)
        self.assertEqual(again.sample, plan.sample)

    def test_sample_size_is_the_larger_of_the_minimum_and_the_fraction(self):
        ids = [f"{index:032x}" for index in range(400)]
        plan = R.plan_release(enabled=True, as_of=self.NOW, context="ctx", current_ids=ids, facts=facts(ids),
                              discovery_by_id={event_id: at("2025-01-01T00:00:00Z") for event_id in ids},
                              previous_fingerprints=document(ids), full_rebuild_hours=24.0, follow_up_window_days=180.0,
                              sample_fraction=0.05, sample_minimum=3)
        self.assertEqual(len(plan.sample), 20)

    def test_confirm_plan_drops_dossiers_the_previous_release_does_not_hold(self):
        plan = self.plan()
        previous = R.PreviousRelease(index={}, index_ids=list(self.IDS), manifest={}, chunk_meta={}, part_meta={}, sha_by_id={},
                                     slim_by_id={}, compressed_by_id={event_id: b"" for event_id in self.IDS[:-1]},
                                     index_minis={event_id: {} for event_id in self.IDS[:-1]}, bucket_members={},
                                     semantic_checksum="", assembled_checksum="", digest_by_name={}, fingerprints=None, patterns={})
        confirmed = R.confirm_plan(plan, previous, as_of=self.NOW)
        self.assertEqual(confirmed.rebuilt, {self.IDS[-1]})
        self.assertEqual(R.confirm_plan(self.plan(), None, as_of=self.NOW).mode, "full")

    def test_fingerprints_document_parses_and_rejects_shapes(self):
        doc = R.fingerprints_document(generated_at="2026-11-24T07:00:00Z", score_as_of="2026-11-24T07:00:00Z", context="c" * 64,
                                      facts=facts(self.IDS), ids=self.IDS, build={"mode": "full", "full_rebuild_at": "x"})
        raw = json.dumps(doc).encode()
        self.assertIsNotNone(R.parse_fingerprints_document(raw))
        self.assertIsNone(R.parse_fingerprints_document(raw.replace(b"a" * 32, b"zz")))
        self.assertIsNone(R.parse_fingerprints_document(b"{}"))
        self.assertIsNone(R.parse_fingerprints_document(None))


class StoreAndChunkTests(unittest.TestCase):
    def test_store_round_trip_and_order(self):
        store = R.DossierStore()
        a = {"event_id": "a", "x": 1}
        b = {"event_id": "b", "x": [1, 2]}
        store.put_canonical("a", R.canonical_json(a))
        store.put_compressed("b", zlib.compress(R.canonical_json(b), 1), hashlib.sha256(R.canonical_json(b)).hexdigest(), reused=True)
        self.assertEqual(store.order, ["a", "b"])
        self.assertEqual([c for _, c, _ in store.iter_decoded()], [a, b])
        self.assertEqual(store.sha256("a"), hashlib.sha256(R.canonical_json(a)).hexdigest())
        self.assertEqual(store.reused, {"b"})
        with self.assertRaises(ValueError):
            store.put_canonical("a", b"{}")

    def _chunk_set(self, store, previous=None, blobs=None):
        written = {}
        result = R.write_chunks(
            store=store, bucket_of=lambda event_id: event_id[:3], bucket_count=4096, chunk_schema=X.CANDIDATE_CHUNK_SCHEMA,
            encode_chunk=X.encode_chunk, decode_chunk=X.decode_chunk, shard_max_bytes=X.CANDIDATE_SHARD_TARGET_MAX_BYTES,
            write=lambda path, raw: written.__setitem__(path, raw), previous=previous,
            previous_blob=lambda path: (blobs or {}).get(path),
        )
        return result, written

    def test_chunks_are_written_from_the_store_and_reused_when_unchanged(self):
        ids = ["0ab" + "0" * 33, "0ab" + "1" * 33, "0ac" + "2" * 33]
        first = R.DossierStore()
        for event_id in ids:
            first.put_canonical(event_id, R.canonical_json({"event_id": event_id, "name": "n" + event_id[-1]}))
        result, written = self._chunk_set(first)
        self.assertTrue(result.faithful)
        self.assertEqual(result.written_buckets, 4096)
        self.assertEqual(result.bucket_counts["0ab"], 2)
        # A previous release holding these bytes; the second store reuses event 0ab...0 and 0ab...1
        # but rebuilt 0ac...2 (same bytes): bucket 0ab is copied, bucket 0ac is encoded again.
        previous = R.PreviousRelease(
            index={}, index_ids=ids, manifest={}, part_meta={},
            chunk_meta={row["path"].split("/")[-1][:-5]: row for row in result.chunk_rows},
            sha_by_id={}, slim_by_id={}, compressed_by_id={}, index_minis={}, bucket_members=result.bucket_members,
            semantic_checksum="", assembled_checksum="", digest_by_name={}, fingerprints=None, patterns={},
        )
        second = R.DossierStore()
        for event_id in ids[:2]:
            second.put_compressed(event_id, zlib.compress(first.canonical(event_id), 1), first.sha256(event_id), reused=True)
        second.put_canonical(ids[2], first.canonical(ids[2]))
        again, written_again = self._chunk_set(second, previous, written)
        self.assertTrue(again.faithful)
        self.assertEqual(again.reused_buckets, 4095)
        self.assertEqual(again.written_buckets, 1)
        self.assertEqual(written_again, written)
        self.assertEqual(again.chunk_rows, result.chunk_rows)
        # A previous chunk whose bytes do not match its manifest row is not reused.
        tampered = dict(written)
        tampered["ctas/data/candidate-chunks/0ab.json"] = written["ctas/data/candidate-chunks/0ab.json"] + b" "
        third, _ = self._chunk_set(second, previous, tampered)
        self.assertEqual(third.reused_buckets, 4094)
        self.assertTrue(third.faithful)

    def test_index_mini_keeps_what_the_multimessenger_index_reads(self):
        candidate = {
            "event_id": "e", "name": "n", "status": "candidate", "ra_deg": 1.0, "dec_deg": 2.0, "discovery_time": "2026-01-01T00:00:00Z",
            "primary_messenger": "optical", "follow_up": {"observations": [{"magnitude": 18.2, "detection": 1, "band": "r"}, {"detection": 0}]},
            "host_association": {"runs": [{"analysisType": "prior-activity", "status": "complete", "result": {"flagged": True, "x": 1}},
                                          {"analysisType": "host-posterior", "status": "complete"}]},
            "lightcurve_science": {"big": "block"},
        }
        mini = R.index_mini(candidate)
        self.assertEqual(mini["follow_up"]["observations"], [{"magnitude": 18.2, "detection": 1}, {"detection": 0}])
        self.assertEqual(mini["host_association"]["runs"], [{"analysisType": "prior-activity", "status": "complete", "result": {"flagged": True}}])
        self.assertNotIn("lightcurve_science", mini)
        multimessenger = importlib.import_module("ctas_multimessenger")
        self.assertEqual(multimessenger.public_index([mini]), multimessenger.public_index([candidate]))


class ReuseCheckTests(unittest.TestCase):
    def test_reports_the_build_and_fails_only_on_a_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "f.json"
            path.write_text(json.dumps({"build": {"mode": "incremental", "reason": "r", "reused": 3, "rebuilt": 1,
                                                  "sample": {"size": 2, "mismatches": 0}}}))
            good = subprocess.run([sys.executable, str(ROOT / "scripts/ctas_reuse_check.py"), str(path)], capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stdout)
            self.assertIn("reused 3, rebuilt 1", good.stdout)
            path.write_text(json.dumps({"build": {"mode": "full", "reason": "r", "reused": 0, "rebuilt": 4,
                                                  "sample": {"size": 2, "mismatches": 1, "mismatched_event_ids": ["x"]},
                                                  "sample_mismatch_at": "2026-11-24T07:00:00Z"}}))
            bad = subprocess.run([sys.executable, str(ROOT / "scripts/ctas_reuse_check.py"), str(path)], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("mismatched event ids: x", bad.stdout)


class WiringTests(unittest.TestCase):
    def test_publisher_schema_and_validator_know_the_fingerprints_file(self):
        publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        self.assertIn("ctas/data/input-fingerprints.json", publisher)
        self.assertIn("check ctas.incremental-reuse python3 scripts/ctas_reuse_check.py", publisher)
        classes = json.loads((ROOT / "scripts/check-classes.json").read_text())["checks"]
        self.assertEqual(classes["ctas.incremental-reuse"]["class"], "advisory")
        validator = (ROOT / "scripts/validate_ctas_schemas.py").read_text()
        self.assertIn('"input-fingerprints.json": ("input_fingerprints_path", "input-fingerprints")', validator)
        schema = json.loads((ROOT / "ctas/schemas/input-fingerprints.schema.json").read_text())
        self.assertEqual(schema["properties"]["schema"]["const"], R.INPUT_FINGERPRINTS_SCHEMA)
        self.assertIn("input-fingerprints", (ROOT / "ctas/openapi.yaml").read_text())
        for path in ("scripts/ctas_release_build.py", "scripts/test_ctas_release_build.py", "scripts/ctas_reuse_check.py"):
            self.assertIn(path, X.BOUND_CODE_PATHS)
        self.assertIn("build", json.loads((ROOT / "ctas/schemas/certification.schema.json").read_text())["properties"])
        self.assertIn("input_fingerprints_path", json.loads((ROOT / "ctas/schemas/status.schema.json").read_text())["properties"])


class EndToEndTests(unittest.TestCase):
    """Three releases of the fixture: a base, then a full and an incremental release at a later
    clock against the base, byte-identical to each other. The incremental one rebuilds the
    event whose observation row changed and the event discovered minutes ago, reuses the rest,
    and copies every unchanged chunk from the base."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        cls.database = base / "fixture.db"
        with gzip.open(FIXTURE, "rb") as source, cls.database.open("wb") as target:
            shutil.copyfileobj(source, target)
        with sqlite3.connect(cls.database) as con:
            # The fixture predates the broker-evidence table; without it every event reads as
            # unreadable (rebuilt), which is the conservative rule but not what this test is about.
            con.execute("CREATE TABLE IF NOT EXISTS source_query_response_evidence (id INTEGER PRIMARY KEY, attempt_id INTEGER, "
                        "sequence INTEGER, response_body BLOB, response_checksum TEXT, parser_contract TEXT, request_url TEXT, received_at TEXT)")
            rows = con.execute("SELECT id, preferred_name, discovery_time FROM events WHERE COALESCE(simulation, 0) = 0 "
                               "AND INSTR(preferred_name, '%') = 0 ORDER BY COALESCE(updated_at, discovery_time) DESC").fetchall()
            with_rows = {str(row[0]) for row in con.execute("SELECT DISTINCT event_id FROM observations WHERE data_rights IN ('public', 'open')")}
            cls.changed_event = next(str(row[0]) for row in reversed(rows) if str(row[0]) in with_rows)
            cls.fresh_event = next(str(row[0]) for row in reversed(rows) if str(row[0]) != cls.changed_event)
            con.execute("UPDATE events SET discovery_time = ? WHERE id = ?", ("2026-11-24T05:30:00", cls.fresh_event))
        cls.repo = base / "repo"
        cls.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=cls.repo, check=True)
        cls.clock0, cls.clock1 = "2026-11-24T06:00:00Z", "2026-11-24T07:00:00Z"
        cls.base_out = cls.export("base", cls.clock0, {})
        (cls.repo / "ctas").mkdir()
        shutil.copytree(cls.base_out, cls.repo / "ctas/data")
        subprocess.run(["git", "add", "-A"], cwd=cls.repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=cls.repo, check=True)
        cls.ref = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cls.repo, text=True).strip()
        with sqlite3.connect(cls.database) as con:
            cursor = con.execute("UPDATE observations SET magnitude = COALESCE(magnitude, 18.0) + 0.001 WHERE event_id = ? AND id = "
                                 "(SELECT MIN(id) FROM observations WHERE event_id = ? AND data_rights IN ('public', 'open'))",
                                 (cls.changed_event, cls.changed_event))
            assert cursor.rowcount == 1, "the fixture event must have a public observation to change"
        environment = {"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(cls.repo / ".git/objects")}
        cls.full_out = cls.export("full", cls.clock1, {**environment, "CTAS_INCREMENTAL": "0"})
        cls.inc_out = cls.export("inc", cls.clock1, environment)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def export(cls, label, clock, environment):
        out = Path(cls.temporary.name) / label
        bulk = Path(cls.temporary.name) / (label + "-bulk")
        command = [sys.executable, str(ROOT / "scripts/export_ctas_snapshot.py"), "--database", str(cls.database),
                   "--output-dir", str(out), "--bulk-dir", str(bulk)]
        if environment.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"):
            command += ["--release-base-ref", cls.ref]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                env={**os.environ, "CTAS_RELEASE_CLOCK": clock, "CTAS_SAMPLE_MINIMUM": "3", **environment})
        if result.returncode != 0:
            raise AssertionError(f"exporter failed ({label}):\n{result.stdout[-2000:]}\n{result.stderr[-4000:]}")
        (out / "stdout.txt").write_text(result.stdout)
        return out

    @staticmethod
    def files(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and p.name != "stdout.txt"}

    def test_incremental_release_is_byte_identical_to_the_full_release(self):
        full, inc = self.files(self.full_out), self.files(self.inc_out)
        self.assertEqual(set(full), set(inc))
        differing = sorted(path for path in full if full[path] != inc[path])
        # The fingerprints document records how each release was built, and the report and
        # status restate that record and bind that document; every other byte is the same.
        self.assertEqual(differing, ["certification.json", "input-fingerprints.json", "status.json"])
        full_doc, inc_doc = json.loads(full["input-fingerprints.json"]), json.loads(inc["input-fingerprints.json"])
        self.assertEqual(full_doc["fingerprints"], inc_doc["fingerprints"])
        self.assertEqual(full_doc["context_digest"], inc_doc["context_digest"])
        self.assertEqual(full_doc["build"]["mode"], "full")
        self.assertEqual(inc_doc["build"]["mode"], "incremental")

        def without_build(raw, kind):
            document = json.loads(raw)
            document.pop("build", None)
            if kind == "certificate":
                document["files"].pop(R.INPUT_FINGERPRINTS_PATH, None)
                document.pop("content_release_id", None)
                document.pop("report_checksum_sha256", None)
            else:
                document["artifacts"].pop("input_fingerprints", None)
                document["artifacts"].pop("certification", None)   # its checksum covers the build record
                document.pop("static_snapshot_verification", None)
            return document

        self.assertEqual(without_build(full["certification.json"], "certificate"), without_build(inc["certification.json"], "certificate"))
        self.assertEqual(without_build(full["status.json"], "status"), without_build(inc["status.json"], "status"))
        full_gates = {gate["id"]: gate["passed"] for gate in json.loads(full["certification.json"])["gates"]}
        inc_gates = {gate["id"]: gate["passed"] for gate in json.loads(inc["certification.json"])["gates"]}
        self.assertEqual(full_gates, inc_gates)

    def test_the_plan_rebuilt_exactly_the_changed_and_the_fresh_event(self):
        build = json.loads((self.inc_out / "input-fingerprints.json").read_text())["build"]
        self.assertEqual(build["decisions"]["changed"], 1, build)
        self.assertEqual(build["decisions"]["clock"], 1, build)
        self.assertEqual(build["decisions"]["new"], 0)
        self.assertEqual(build["decisions"]["unreadable"], 0)
        self.assertEqual(build["rebuilt"], 2 + build["decisions"]["cross-event"])
        self.assertEqual(build["sample"]["mismatches"], 0)
        self.assertEqual(build["sample"]["size"], 3)
        base_doc = json.loads((self.base_out / "input-fingerprints.json").read_text())
        inc_doc = json.loads((self.inc_out / "input-fingerprints.json").read_text())
        changed = [event_id for event_id, value in inc_doc["fingerprints"].items() if base_doc["fingerprints"].get(event_id) != value]
        self.assertEqual(changed, [self.changed_event])
        self.assertEqual(build["full_rebuild_at"], self.clock0)
        stdout = (self.inc_out / "stdout.txt").read_text()
        self.assertIn("chunk files reused", stdout)
        self.assertRegex(stdout, r"(409[0-6]) chunk files reused")

    def test_the_release_declares_and_binds_the_fingerprints(self):
        status = json.loads((self.inc_out / "status.json").read_text())
        certificate = json.loads((self.inc_out / "certification.json").read_text())
        self.assertEqual(status["input_fingerprints_path"], R.INPUT_FINGERPRINTS_PATH)
        self.assertEqual(status["build"]["mode"], "incremental")
        self.assertIn(R.INPUT_FINGERPRINTS_PATH, certificate["files"])
        self.assertEqual(certificate["build"]["mode"], "incremental")
        raw = (self.inc_out / "input-fingerprints.json").read_bytes()
        self.assertEqual(certificate["files"][R.INPUT_FINGERPRINTS_PATH]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(status["artifacts"]["input_fingerprints"]["sha256"], hashlib.sha256(raw).hexdigest())

    def test_a_changed_context_forces_a_full_rebuild(self):
        environment = {"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.repo / ".git/objects"), "CTAS_EXPORT_HEAVY": "0"}
        out = self.export("light", self.clock1, environment)
        build = json.loads((out / "input-fingerprints.json").read_text())["build"]
        self.assertEqual(build["mode"], "full")
        self.assertIn("changed", build["reason"])

    def test_reuse_check_passes_on_the_incremental_release(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/ctas_reuse_check.py"), str(self.inc_out / "input-fingerprints.json")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("build incremental", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
