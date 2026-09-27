#!/usr/bin/env python3
"""Write the integrity manifest for the static, citation-only QualQuest export."""
from pathlib import Path
import hashlib
import json


ROOT = Path(__file__).resolve().parents[1] / "qualquest"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


files = {}
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or path.name == "release-manifest.json":
        continue
    if path.is_symlink():
        raise RuntimeError(f"Symlink in export: {path.relative_to(ROOT)}")
    files[str(path.relative_to(ROOT))] = digest(path)


def load(name: str):
    return json.loads((ROOT / "data" / name).read_text())


catalog = load("textbook-index.json")
methods = load("question-types.json")
reference = load("reference-data.json")
questions = [item for item in catalog["problems"] if item["kind"] == "question"]
manifest = {
    "schema": "qualquest-release.v1",
    "files": files,
    "questions": len(questions),
    "workedExamples": len(catalog["problems"]) - len(questions),
    "methodTypes": len(methods["methods"]),
    "flashcards": len(reference["flashcards"]),
}
(ROOT / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"Wrote QualQuest manifest for {len(files)} assets.")
