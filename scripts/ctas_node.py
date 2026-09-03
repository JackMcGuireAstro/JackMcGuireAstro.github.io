#!/usr/bin/env python3
"""Locate a JavaScript runtime the way a background publisher has to.

A launchd job inherits a minimal PATH, so a Mac with Node installed through
nvm, Homebrew, Volta or n reports `node: command not found` even though the
runtime is right there. The browser-side modules are part of the published
release and must be exercised before a release is committed, so the publisher
looks in the usual install locations before it concludes there is no runtime
at all.

    python3 scripts/ctas_node.py     # prints the resolved path, or nothing
"""
from __future__ import annotations

import functools
import glob
import os
import shutil
from pathlib import Path

# Ordered by how a Mac is usually set up: an explicit override, then PATH, then
# the package managers that keep their binaries outside a login shell's PATH.
SEARCH_PATTERNS: tuple[str, ...] = (
    "/opt/homebrew/bin/node",
    "/usr/local/bin/node",
    "/usr/bin/node",
    "~/.volta/bin/node",
    "~/.nvm/versions/node/*/bin/node",
    "/opt/homebrew/opt/node@*/bin/node",
    "/usr/local/opt/node@*/bin/node",
    "/usr/local/n/versions/node/*/bin/node",
    "/opt/local/bin/node",
)


def _version_key(path: str) -> tuple:
    """Sort v20.11.1 above v9.11.2, which a plain string sort gets backwards."""
    for part in Path(path).parts:
        if part.startswith("v") and part[1:2].isdigit():
            numbers = []
            for chunk in part[1:].split("."):
                numbers.append(int(chunk) if chunk.isdigit() else 0)
            return tuple(numbers)
    return ()


@functools.lru_cache(maxsize=1)
def node_binary() -> str | None:
    """Return an executable node, or None when this machine genuinely has none."""
    override = os.environ.get("CTAS_NODE")
    if override and os.access(override, os.X_OK):
        return override
    found = shutil.which("node")
    if found:
        return found
    for pattern in SEARCH_PATTERNS:
        matches = sorted(glob.glob(os.path.expanduser(pattern)), key=_version_key, reverse=True)
        for candidate in matches:
            if os.access(candidate, os.X_OK):
                return candidate
    return None


if __name__ == "__main__":
    resolved = node_binary()
    if resolved:
        print(resolved)
    raise SystemExit(0 if resolved else 1)
