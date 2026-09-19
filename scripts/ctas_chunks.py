"""Lossless bounded-file storage for complete CTAS candidate chunks.

Logical bucket paths remain stable. Oversized logical chunks point to ordered,
individually verified JSON fragments, including when one candidate is itself
larger than the file budget. Fragments are transport details, not truncated
scientific records.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

CHUNK_SCHEMA = "ctas.public-candidate-chunk@1.0.0"
DESCRIPTOR_SCHEMA = "ctas.candidate-chunk-parts@1.0.0"
PART_SCHEMA = "ctas.candidate-json-part@1.0.0"
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
FRAGMENT_TARGET_CHARACTERS = 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BUCKET = re.compile(r"[0-9a-f]{2,4}")
_DESCRIPTOR_BUCKET = re.compile(r"[0-9a-f]{3}")


def _json_bytes(document: Any) -> bytes:
    return (json.dumps(document, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _read_json(raw: bytes, label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise ValueError(f"{label} must be bytes")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return document


def _integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _validate_chunk(document: Any) -> None:
    if not isinstance(document, dict) or document.get("schema") != CHUNK_SCHEMA:
        raise ValueError("unsupported candidate chunk schema")
    bucket = document.get("bucket")
    if not isinstance(bucket, str) or _BUCKET.fullmatch(bucket) is None:
        raise ValueError("invalid candidate chunk bucket")
    rows = document.get("candidates")
    if (not isinstance(rows, list) or not _integer(document.get("candidate_count"))
            or document["candidate_count"] != len(rows)
            or not all(isinstance(row, dict) for row in rows)):
        raise ValueError("invalid candidate chunk candidates or count")


def encode_chunk(document: dict, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[bytes, dict[str, bytes]]:
    """Return a bounded logical root and any bounded supplemental part files."""
    if not _integer(max_bytes, 1):
        raise ValueError("max_bytes must be a positive integer")
    _validate_chunk(document)
    raw = _json_bytes(document)
    if len(raw) <= max_bytes:
        return raw, {}
    bucket = document["bucket"]
    if _DESCRIPTOR_BUCKET.fullmatch(bucket) is None:
        raise ValueError("multipart chunks require a three-digit bucket")
    text = raw.decode("ascii")
    supplemental: dict[str, bytes] = {}
    metadata = []
    offset = 0
    ordinal = 1
    while offset < len(text):
        if ordinal > 999999:
            raise ValueError("candidate chunk exceeds the multipart index capacity")

        def part_bytes(length: int) -> bytes:
            return _json_bytes({"schema": PART_SCHEMA, "bucket": bucket,
                                "part": ordinal, "json_fragment": text[offset:offset + length]})

        # JSON-escaping the string can increase its stored length. Measure the
        # actual envelope and use a binary search if the first slice is too big.
        length = min(FRAGMENT_TARGET_CHARACTERS, len(text) - offset)
        encoded = part_bytes(length)
        if len(encoded) > max_bytes:
            low, high = 0, length
            while low < high:
                middle = (low + high + 1) // 2
                if len(part_bytes(middle)) <= max_bytes:
                    low = middle
                else:
                    high = middle - 1
            length = low
            if length == 0:
                raise ValueError("file budget cannot hold one candidate JSON fragment")
            encoded = part_bytes(length)
        path = f"ctas/data/candidate-chunks/{bucket}.part-{ordinal:06d}.json"
        supplemental[path] = encoded
        metadata.append({"path": path, "bytes": len(encoded),
                         "sha256": hashlib.sha256(encoded).hexdigest()})
        offset += length
        ordinal += 1
    descriptor = _json_bytes({"schema": DESCRIPTOR_SCHEMA, "bucket": bucket,
                              "candidate_count": document["candidate_count"],
                              "assembled_bytes": len(raw),
                              "assembled_sha256": hashlib.sha256(raw).hexdigest(),
                              "parts": metadata})
    if len(descriptor) > max_bytes:
        raise ValueError("candidate chunk part descriptor exceeds the file budget")
    return descriptor, supplemental


def decode_chunk(raw: bytes, read_artifact: Callable[[str], bytes]) -> dict:
    """Read an ordinary or multipart logical chunk, verifying all stored bytes."""
    document = _read_json(raw, "candidate chunk")
    if document.get("schema") != DESCRIPTOR_SCHEMA:
        _validate_chunk(document)
        return document
    bucket = document.get("bucket")
    if not isinstance(bucket, str) or _DESCRIPTOR_BUCKET.fullmatch(bucket) is None:
        raise ValueError("invalid multipart candidate bucket")
    if (not _integer(document.get("candidate_count"))
            or not _integer(document.get("assembled_bytes"), 1)
            or not _sha(document.get("assembled_sha256"))):
        raise ValueError("invalid assembled candidate chunk metadata")
    parts = document.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > 999999:
        raise ValueError("invalid candidate chunk parts")
    fragments: list[str] = []
    assembled_length = 0
    for ordinal, metadata in enumerate(parts, 1):
        expected_path = f"ctas/data/candidate-chunks/{bucket}.part-{ordinal:06d}.json"
        if (not isinstance(metadata, dict) or metadata.get("path") != expected_path
                or not _integer(metadata.get("bytes"), 1) or not _sha(metadata.get("sha256"))):
            raise ValueError("candidate chunk parts must have safe, unique, contiguous ordered paths")
        try:
            part_raw = read_artifact(expected_path)
        except (OSError, KeyError, ValueError) as exc:
            raise ValueError(f"cannot read candidate chunk part: {expected_path}") from exc
        if (not isinstance(part_raw, bytes) or len(part_raw) != metadata["bytes"]
                or hashlib.sha256(part_raw).hexdigest() != metadata["sha256"]):
            raise ValueError(f"candidate chunk part bytes or checksum mismatch: {expected_path}")
        part = _read_json(part_raw, "candidate chunk part")
        fragment = part.get("json_fragment")
        if (part.get("schema") != PART_SCHEMA or part.get("bucket") != bucket
                or type(part.get("part")) is not int or part["part"] != ordinal
                or not isinstance(fragment, str) or not fragment or not fragment.isascii()):
            raise ValueError(f"invalid candidate chunk part content: {expected_path}")
        assembled_length += len(fragment)
        if assembled_length > document["assembled_bytes"]:
            raise ValueError("assembled candidate chunk exceeds its declared byte length")
        fragments.append(fragment)
    assembled = "".join(fragments).encode("ascii")
    if (len(assembled) != document["assembled_bytes"]
            or hashlib.sha256(assembled).hexdigest() != document["assembled_sha256"]):
        raise ValueError("assembled candidate chunk bytes or checksum mismatch")
    result = _read_json(assembled, "assembled candidate chunk")
    _validate_chunk(result)
    if result["bucket"] != bucket or result["candidate_count"] != document["candidate_count"]:
        raise ValueError("assembled candidate chunk identity or count mismatch")
    return result
