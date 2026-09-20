#!/usr/bin/env python3
"""Verify the exported study app and the public-document boundary before deploy."""
from pathlib import Path
import hashlib, json
ROOT = Path(__file__).resolve().parents[1] / 'qualquest'
manifest = json.loads((ROOT/'release-manifest.json').read_text())
actual = {str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and p.name != 'release-manifest.json'}
assert actual == set(manifest['files']), 'Unlisted or missing QualQuest assets'
for rel, expected in manifest['files'].items():
    p = ROOT/rel
    assert not p.is_symlink(), f'Symlink in export: {rel}'
    assert p.suffix.lower() not in {'.pdf','.doc','.docx','.tex','.epub','.key','.pem'}, f'Document or secret asset: {rel}'
    assert hashlib.sha256(p.read_bytes()).hexdigest() == expected, f'Changed release asset: {rel}'
load = lambda name: json.loads((ROOT/'data'/name).read_text())
catalog, methods, reader, reference = map(load, ['textbook-index.json','question-types.json','reader-index.json','reference-data.json'])
questions = {p['id'] for p in catalog['problems'] if p['kind'] == 'question'}
assert len(questions) == manifest['questions']
assert len(catalog['problems']) - len(questions) == manifest['workedExamples']
assert len(methods['methods']) == manifest['methodTypes']
assert len(reference['flashcards']) == manifest['flashcards']
assert set().union(*(set(m['questionIds']) for m in methods['methods'])) == questions
assert {p['id'] for p in reader['pages']} == {p['id'] for p in catalog['problems']}
for p in catalog['problems']:
    assert set(p) <= {'id','bookKey','chapter','number','kind','section','subject','courseLevel'}, 'Non-citation field in public question inventory'
for m in methods['methods']:
    assert set(m) <= {'id','title','principle','steps','boundary','questionIds'}
for p in reader['pages']:
    assert set(p) == {'id','bookKey','pageNumber'} and isinstance(p['pageNumber'],int) and p['pageNumber'] > 0
for name in ['index.html','question-reader.html','question-reader.mjs','question-reader.css']:
    assert (ROOT/name).is_file()
print(f"QualQuest verified: {len(questions):,} questions, {len(methods['methods'])} types, {len(reference['flashcards'])} cards; all {len(actual)} public files match the reviewed export.")
