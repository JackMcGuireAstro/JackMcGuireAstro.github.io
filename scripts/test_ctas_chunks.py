#!/usr/bin/env python3
"""Regression tests for lossless CTAS chunks larger than the publication budget."""
import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from ctas_chunks import CHUNK_SCHEMA, DESCRIPTOR_SCHEMA, PART_SCHEMA, encode_chunk, decode_chunk
except ModuleNotFoundError:
    from scripts.ctas_chunks import CHUNK_SCHEMA, DESCRIPTOR_SCHEMA, PART_SCHEMA, encode_chunk, decode_chunk


def raw_json(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def chunk(rows, bucket="efe"):
    return {"schema": CHUNK_SCHEMA, "bucket": bucket,
            "candidate_count": len(rows), "candidates": rows}


class CandidateChunkCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.large = chunk([{"event_id": "single-oversized-candidate", "name": "AT2026zjy",
                            "retained_payload": "x" * 5_600_000,
                            "unicode": "α Centauri • 波長 🌠",
                            "receipts": [{"id": "q1", "measurements": [0, -1.2, None]},
                                         {"id": "q2", "value": "quote: \" and slash: \\"}]}])
        cls.root, cls.parts = encode_chunk(cls.large)

    def test_single_oversized_record_round_trips_without_losing_evidence(self):
        descriptor = json.loads(self.root)
        self.assertEqual(descriptor["schema"], DESCRIPTOR_SCHEMA)
        self.assertGreater(descriptor["assembled_bytes"], 5_600_000)
        self.assertEqual(descriptor["candidate_count"], 1)
        self.assertGreater(len(self.parts), 1)
        self.assertLessEqual(len(self.root), 4 * 1024 * 1024)
        self.assertTrue(all(len(raw) <= 4 * 1024 * 1024 for raw in self.parts.values()))
        self.assertEqual(decode_chunk(self.root, self.parts.__getitem__), self.large)
        reconstructed = "".join(json.loads(self.parts[part["path"]])["json_fragment"]
                                for part in descriptor["parts"]).encode("ascii")
        self.assertEqual(reconstructed, raw_json(self.large))

    def test_ordinary_and_legacy_buckets_preserve_existing_bytes(self):
        for bucket in ("ef", "efe", "0efe"):
            for rows in ([], [{"event_id": "x", "value": "Ω"}]):
                with self.subTest(bucket=bucket, rows=rows):
                    document = chunk(rows, bucket)
                    root, parts = encode_chunk(document)
                    self.assertEqual(root, raw_json(document))
                    self.assertEqual(parts, {})
                    self.assertEqual(decode_chunk(root, lambda _: self.fail("ordinary chunk read parts")), document)

    def test_fragment_files_are_deterministic(self):
        root, parts = encode_chunk(self.large)
        self.assertEqual(root, self.root)
        self.assertEqual(parts, self.parts)
        descriptor = json.loads(root)
        self.assertEqual([row["path"] for row in descriptor["parts"]], sorted(parts))

    def test_exact_budget_boundary_and_one_byte_over(self):
        document = chunk([{"event_id": "boundary", "payload": "\\\"α" * 12000}])
        size = len(raw_json(document))
        root, parts = encode_chunk(document, max_bytes=size)
        self.assertEqual(len(root), size)
        self.assertEqual(parts, {})
        root, parts = encode_chunk(document, max_bytes=size - 1)
        self.assertTrue(parts)
        self.assertTrue(all(len(raw) <= size - 1 for raw in [root, *parts.values()]))
        self.assertEqual(decode_chunk(root, parts.__getitem__), document)

    def test_missing_corrupt_and_truncated_files_are_rejected(self):
        path = next(iter(self.parts))
        variants = {}
        missing = dict(self.parts)
        del missing[path]
        variants["missing"] = missing
        variants["corrupt"] = {**self.parts, path: b"x" + self.parts[path][1:]}
        variants["truncated"] = {**self.parts, path: self.parts[path][:-1]}
        for label, parts in variants.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                decode_chunk(self.root, parts.__getitem__)

    def test_reordered_duplicate_cross_bucket_and_unsafe_paths_are_rejected(self):
        original = json.loads(self.root)
        variants = {}
        reordered = copy.deepcopy(original)
        reordered["parts"][0], reordered["parts"][1] = reordered["parts"][1], reordered["parts"][0]
        variants["reordered"] = reordered
        duplicate = copy.deepcopy(original)
        duplicate["parts"][1] = duplicate["parts"][0]
        variants["duplicate"] = duplicate
        for path in ("ctas/data/candidate-chunks/abc.part-000001.json",
                     "ctas/data/candidate-chunks/../efe.part-000001.json",
                     "/ctas/data/candidate-chunks/efe.part-000001.json",
                     "https://example.com/efe.part-000001.json",
                     "ctas/data/candidate-chunks/efe.part-000002.json"):
            changed = copy.deepcopy(original)
            changed["parts"][0]["path"] = path
            variants[path] = changed
        for label, descriptor in variants.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                decode_chunk(raw_json(descriptor), self.parts.__getitem__)

    def test_part_schema_bucket_ordinal_and_fragment_types_are_checked(self):
        path = next(iter(self.parts))
        original_part = json.loads(self.parts[path])
        for field, value in (("schema", "wrong"), ("bucket", "abc"), ("part", 2),
                             ("part", True), ("json_fragment", None), ("json_fragment", ""),
                             ("json_fragment", "α")):
            with self.subTest(field=field, value=value):
                part = {**original_part, field: value}
                raw = raw_json(part)
                descriptor = json.loads(self.root)
                descriptor["parts"][0]["bytes"] = len(raw)
                descriptor["parts"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
                with self.assertRaises(ValueError):
                    decode_chunk(raw_json(descriptor), {**self.parts, path: raw}.__getitem__)

    def test_assembled_length_hash_and_candidate_count_are_checked(self):
        original = json.loads(self.root)
        for field, value in (("assembled_bytes", original["assembled_bytes"] + 1),
                             ("assembled_bytes", original["assembled_bytes"] - 1),
                             ("assembled_sha256", "0" * 64), ("candidate_count", 2)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                decode_chunk(raw_json({**original, field: value}), self.parts.__getitem__)
        truncated = copy.deepcopy(original)
        truncated["parts"].pop()
        with self.assertRaises(ValueError):
            decode_chunk(raw_json(truncated), self.parts.__getitem__)

    def test_invalid_metadata_types_are_rejected(self):
        original = json.loads(self.root)
        for field, value in (("bucket", None), ("candidate_count", True),
                             ("assembled_bytes", "5600000"), ("assembled_bytes", True),
                             ("assembled_sha256", None), ("parts", {}), ("parts", [])):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                decode_chunk(raw_json({**original, field: value}), self.parts.__getitem__)
        for field, value in (("bytes", True), ("bytes", "100"), ("sha256", "invalid")):
            descriptor = copy.deepcopy(original)
            descriptor["parts"][0][field] = value
            with self.subTest(partfield=field, value=value), self.assertRaises(ValueError):
                decode_chunk(raw_json(descriptor), self.parts.__getitem__)

    def test_unknown_schema_duplicate_json_fields_and_wrong_raw_types_are_rejected(self):
        for raw in (b'[]', b'{"schema":"unknown"}', b'{"schema":"x","schema":"y"}',
                    '{"schema":"x"}', b'null'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_chunk(raw, self.parts.__getitem__)
        for document in (chunk([[]]), {**chunk([]), "candidate_count": True},
                         {**chunk([]), "candidate_count": 1}, {**chunk([]), "bucket": "../"}):
            with self.subTest(document=document), self.assertRaises(ValueError):
                encode_chunk(document)

    def test_invalid_and_impossibly_small_budgets_fail_explicitly(self):
        for budget in (0, -1, True, None, "4194304", 1):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                encode_chunk(chunk([{"payload": "x" * 100}]), budget)


class MultipartHistoryReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import export_ctas_snapshot as exporter
        except ModuleNotFoundError:
            from scripts import export_ctas_snapshot as exporter
        cls.exporter = exporter
        cls.candidate = {"event_id": "history-large", "name": "AT2026abc",
                         "receipts": [{"value": "α • 🌠", "measurements": [0, None, -1.5]}],
                         "retained_content": "x" * 4_500_000}
        big_raw, cls.base_parts = encode_chunk(chunk([cls.candidate], "efe"))
        empty_raw, _ = encode_chunk(chunk([], "000"))
        cls.base_roots = {"ctas/data/candidate-chunks/000.json": empty_raw,
                          "ctas/data/candidate-chunks/efe.json": big_raw}
        index = {"candidate_count": 1, "candidates": [{"event_id": "history-large"}]}
        cls.index_raw = raw_json(index)
        cls.base_manifest, _ = exporter.complete_catalog_manifest_artifact(
            [cls.candidate], index["candidates"], cls.index_raw,
            {"000": [], "efe": [cls.candidate]}, cls.base_roots, "a" * 64, cls.base_parts,
        )

    def setUp(self):
        self.manifest = copy.deepcopy(self.base_manifest)
        self.blobs = {"ctas/data/catalog-index.json": self.index_raw,
                      **self.base_roots, **self.base_parts}

    def load(self):
        self.blobs["ctas/data/candidate-chunks/manifest.json"] = raw_json(self.manifest)
        with patch.object(self.exporter, "git_blob", side_effect=lambda _repo, _ref, path: self.blobs.get(path)):
            return self.exporter.git_catalog_document(Path.cwd(), "history-fixture")

    def test_history_reconstructs_single_record_larger_than_four_mib(self):
        self.assertTrue(self.base_parts)
        result = self.load()
        self.assertIsNotNone(result)
        self.assertEqual(result["candidates"], [self.candidate])

    def test_history_rejects_unknown_schema_and_wrong_or_boolean_counts(self):
        for field, value in (("schema", "unknown"), ("chunk_count", 999),
                             ("chunk_count", True), ("candidate_count", True),
                             ("candidate_count", -1)):
            with self.subTest(field=field, value=value):
                self.manifest = {**copy.deepcopy(self.base_manifest), field: value}
                self.assertIsNone(self.load())

    def test_history_requires_ordered_unique_safe_root_paths(self):
        for defect in ("reorder", "duplicate", "unsafe", "nonstring"):
            self.manifest = copy.deepcopy(self.base_manifest)
            if defect == "reorder":
                self.manifest["chunks"].reverse()
            elif defect == "duplicate":
                self.manifest["chunks"].append(self.manifest["chunks"][0])
                self.manifest["chunk_count"] += 1
            elif defect == "unsafe":
                self.manifest["chunks"][0]["path"] = "ctas/data/candidate-chunks/../000.json"
            else:
                self.manifest["chunks"][0]["path"] = None
            with self.subTest(defect=defect):
                self.assertIsNone(self.load())

    def test_history_root_filename_must_match_decoded_bucket(self):
        old = "ctas/data/candidate-chunks/efe.json"
        new = "ctas/data/candidate-chunks/fff.json"
        self.blobs[new] = self.blobs.pop(old)
        self.manifest["chunks"][1]["path"] = new
        self.assertIsNone(self.load())

    def test_history_rejects_missing_corrupt_and_truncated_part_files(self):
        path = self.manifest["parts"][0]["path"]
        original = self.blobs[path]
        del self.blobs[path]
        self.assertIsNone(self.load())
        for raw in (b"x" + original[1:], original[:-1]):
            self.blobs[path] = raw
            with self.subTest(length=len(raw)):
                self.assertIsNone(self.load())

    def test_history_rejects_undeclared_and_unreachable_parts(self):
        self.manifest["parts"].pop(0)
        self.assertIsNone(self.load())
        self.manifest = copy.deepcopy(self.base_manifest)
        extra = dict(self.manifest["parts"][0])
        extra["path"] = "ctas/data/candidate-chunks/fff.part-000001.json"
        self.manifest["parts"].append(extra)
        self.assertIsNone(self.load())
        self.manifest = copy.deepcopy(self.base_manifest)
        del self.manifest["parts"]
        self.assertIsNone(self.load())

    def test_history_rejects_reordered_duplicate_or_unsafe_global_parts(self):
        for defect in ("reorder", "duplicate", "unsafe"):
            self.manifest = copy.deepcopy(self.base_manifest)
            if defect == "reorder":
                self.manifest["parts"].reverse()
            elif defect == "duplicate":
                self.manifest["parts"].append(self.manifest["parts"][0])
            else:
                self.manifest["parts"][0]["path"] = "ctas/data/candidate-chunks/../efe.part-000001.json"
            with self.subTest(defect=defect):
                self.assertIsNone(self.load())

    def test_history_metadata_types_and_hashes_are_verified(self):
        for container in ("chunks", "parts"):
            for field, value in (("bytes", True), ("bytes", "12"), ("bytes", 1),
                                 ("sha256", None), ("sha256", "0" * 64)):
                self.manifest = copy.deepcopy(self.base_manifest)
                self.manifest[container][0][field] = value
                with self.subTest(container=container, field=field, value=value):
                    self.assertIsNone(self.load())
        self.manifest = copy.deepcopy(self.base_manifest)
        self.manifest["chunks"][0]["candidate_count"] = False
        self.assertIsNone(self.load())
        for value in (None, {}, [], {"path": "ctas/data/catalog-index.json"}):
            self.manifest = copy.deepcopy(self.base_manifest)
            self.manifest["catalog_index"] = value
            with self.subTest(index=value):
                self.assertIsNone(self.load())

    def test_history_accepts_legacy_two_and_four_digit_ordinary_roots(self):
        small = {"event_id": "history-small", "name": "AT2026small"}
        index = {"candidate_count": 1, "candidates": [{"event_id": "history-small"}]}
        index_raw = raw_json(index)
        for bucket in ("ef", "0efe"):
            root, parts = encode_chunk(chunk([small], bucket))
            root_path = f"ctas/data/candidate-chunks/{bucket}.json"
            self.manifest, _ = self.exporter.complete_catalog_manifest_artifact(
                [small], index["candidates"], index_raw, {bucket: [small]},
                {root_path: root}, "a" * 64, parts,
            )
            del self.manifest["parts"]
            self.blobs = {root_path: root, "ctas/data/catalog-index.json": index_raw}
            with self.subTest(bucket=bucket):
                self.assertEqual(self.load()["candidates"], [small])


if __name__ == "__main__":
    unittest.main()
