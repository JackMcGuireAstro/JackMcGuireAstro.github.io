"""The incremental release's self-check, read from ctas/data/input-fingerprints.json after the
export (publish_ctas.sh, check ctas.incremental-reuse).

Prints how the release was built. Exits 1 when a reused dossier the exporter rebuilt for
comparison did not reproduce from the database: the exporter already turned that release
into a full rebuild, so this is an advisory check whose failure reaches an issue, never a
block (scripts/check-classes.json)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "ctas/data/input-fingerprints.json")
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        print(f"{path}: cannot be read ({exc})")
        return 1
    build = document.get("build") or {}
    sample = build.get("sample") or {}
    print(f"build {build.get('mode')}: {build.get('reason')}; reused {build.get('reused')}, rebuilt {build.get('rebuilt')}, "
          f"re-verified {sample.get('size', 0)} with {sample.get('mismatches', 0)} mismatches; "
          f"decisions {json.dumps(build.get('decisions') or {}, sort_keys=True)}; "
          f"last full rebuild {build.get('full_rebuild_at')}")
    if build.get("sample_mismatch_at"):
        print(f"a re-verified dossier last failed to reproduce at {build['sample_mismatch_at']}; full rebuilds continue for "
              f"{build.get('full_rebuild_hours', 24)} h after it")
    if sample.get("mismatches"):
        print("mismatched event ids: " + ", ".join(sample.get("mismatched_event_ids") or []))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
