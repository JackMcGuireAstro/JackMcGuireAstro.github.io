#!/usr/bin/env python3
"""Compressed state copies cut into parts under GitHub's 2 GiB release-asset limit.

GitHub refuses a release asset of 2 GiB or more. A state copy is therefore compressed
straight into parts of PART_BYTES (default 1.5 GiB) beside a small JSON manifest naming
every part with its size and sha256, the sha256 of the joined compressed stream and the
sha256 and size of what it decompresses to. Nothing is held in memory: parts are written,
hashed, joined and decompressed block by block.

The same design as scripts/ctas_cloud_state.py (2026-11-16), as a library for
scripts/worldsindex_cloud_state.py (2026-11-18); CTAS keeps its own copy of these pieces
so that a change here cannot reach its certified release.
"""
from __future__ import annotations

import gzip
import hashlib
import shutil
import subprocess
import tempfile
import threading
import zlib
from pathlib import Path

GIB = 1 << 30
BLOCK = 16 << 20
ZSTD_ARGS = ["-10", "--long=27"]   # --long=27 must be given when decompressing too


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def part_name(base: str, index: int) -> str:
    return f"{base}.part-{index:03d}"


class PartWriter:
    """A file-like sink that cuts what is written into parts of ``part_bytes`` in ``directory``,
    hashing each part and the whole stream as it goes."""

    def __init__(self, directory: Path, base: str, part_bytes: int):
        if part_bytes < 1:
            raise ValueError("part_bytes must be positive")
        self.directory, self.base, self.part_bytes = directory, base, part_bytes
        self.parts: list[dict] = []
        self.whole = hashlib.sha256()
        self.total = 0
        self._handle = None
        self._digest = None
        self._size = 0

    def _open_next(self) -> None:
        self._close_current()
        name = part_name(self.base, len(self.parts))
        self._handle = open(self.directory / name, "wb")
        self._digest = hashlib.sha256()
        self._size = 0
        self.parts.append({"name": name})

    def _close_current(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self.parts[-1].update(bytes=self._size, sha256=self._digest.hexdigest())
            self._handle = None

    def write(self, data: bytes) -> int:
        view = memoryview(data)
        while view:
            if self._handle is None or self._size >= self.part_bytes:
                self._open_next()
            take = view[: self.part_bytes - self._size]
            self._handle.write(take)
            self._digest.update(take)
            self.whole.update(take)
            self._size += len(take)
            self.total += len(take)
            view = view[len(take):]
        return len(data)

    def flush(self) -> None:
        if self._handle is not None:
            self._handle.flush()

    def close(self) -> None:
        if not self.parts:
            self._open_next()   # an empty stream still has one (empty) part
        self._close_current()


class JoinedReader:
    """Reads the parts one after the other as one stream, hashing what it hands out."""

    def __init__(self, paths: list[Path]):
        self.paths = list(paths)
        self.digest = hashlib.sha256()
        self._handle = None

    def read(self, size: int = -1) -> bytes:
        size = BLOCK if size is None or size < 0 else size
        while True:
            if self._handle is None:
                if not self.paths:
                    return b""
                self._handle = open(self.paths.pop(0), "rb")
            data = self._handle.read(size)
            if data:
                self.digest.update(data)
                return data
            self._handle.close()
            self._handle = None

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def compress_into_parts(source: Path, directory: Path, base: str, compression: str, part_bytes: int) -> PartWriter:
    """Compress ``source`` straight into parts (``compression`` is "zstd" or "gzip")."""
    writer = PartWriter(directory, base, part_bytes)
    if compression == "zstd":
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(["zstd", "-q", "-c", "-T0", *ZSTD_ARGS, str(source)],
                                       stdout=subprocess.PIPE, stderr=errors)
            assert process.stdout is not None
            for block in iter(lambda: process.stdout.read(BLOCK), b""):
                writer.write(block)
            process.stdout.close()
            if process.wait() != 0:
                errors.seek(0)
                writer.close()
                raise RuntimeError(f"zstd failed: {errors.read().decode(errors='replace').strip()[:300]}")
    elif compression == "gzip":
        with open(source, "rb") as handle, gzip.GzipFile(fileobj=writer, mode="wb", compresslevel=6) as sink:
            shutil.copyfileobj(handle, sink, BLOCK)
    else:
        raise ValueError(f"unknown compression {compression!r}")
    writer.close()
    return writer


def stream_parts(paths: list[Path], compression: str, sink) -> str:
    """Decompress the joined parts, handing each decompressed block to ``sink``; returns the
    sha256 of the joined compressed stream."""
    reader = JoinedReader(paths)
    try:
        if compression == "zstd":
            with tempfile.TemporaryFile() as errors:
                process = subprocess.Popen(["zstd", "-q", "-d", "-c", "--long=27"],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
                assert process.stdin is not None and process.stdout is not None
                failure: list[BaseException] = []

                def feed() -> None:
                    try:
                        for block in iter(lambda: reader.read(BLOCK), b""):
                            process.stdin.write(block)
                    except BaseException as error:  # a broken pipe when zstd stops early
                        failure.append(error)
                    finally:
                        try:
                            process.stdin.close()
                        except OSError:
                            pass

                feeder = threading.Thread(target=feed, daemon=True)
                feeder.start()
                for block in iter(lambda: process.stdout.read(BLOCK), b""):
                    sink(block)
                process.stdout.close()
                feeder.join()
                if process.wait() != 0 or failure:
                    errors.seek(0)
                    raise RuntimeError(f"the parts do not decompress: {errors.read().decode(errors='replace').strip()[:300]}")
        elif compression == "gzip":
            with gzip.GzipFile(fileobj=reader, mode="rb") as source:
                for block in iter(lambda: source.read(BLOCK), b""):
                    sink(block)
        else:
            raise ValueError(f"unknown compression {compression!r}")
    except (OSError, EOFError, zlib.error) as error:
        raise RuntimeError(f"the parts do not decompress: {error}") from None
    finally:
        reader.close()
    return reader.digest.hexdigest()


def check_parts(directory: Path, manifest: dict) -> list[Path]:
    """Every part beside the manifest has its size and sha256 (one part at a time)."""
    paths = []
    for part in manifest["parts"]:
        path = directory / part["name"]
        if not path.is_file():
            raise RuntimeError(f"part {part['name']} is missing")
        if path.stat().st_size != part["bytes"] or sha256_file(path) != part["sha256"]:
            raise RuntimeError(f"part {part['name']} does not match the manifest (size or sha256)")
        paths.append(path)
    return paths


def join_into(directory: Path, manifest: dict, target: Path) -> None:
    """Join and decompress a checked set into ``target``, checking the joined stream's sha256
    and the payload's sha256 and size against the manifest. ``target`` is removed on any failure."""
    paths = check_parts(directory, manifest)
    digest = hashlib.sha256()
    size = 0
    try:
        with open(target, "wb") as out:
            def sink(block: bytes) -> None:
                nonlocal size
                out.write(block)
                digest.update(block)
                size += len(block)
            stream_sha = stream_parts(paths, manifest["compression"], sink)
        if stream_sha != manifest["sha256"]:
            raise RuntimeError("the joined parts do not match the manifest's sha256")
        if digest.hexdigest() != manifest["payload_sha256"] or size != manifest["payload_bytes"]:
            raise RuntimeError("the restored payload does not match the manifest's sha256")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
