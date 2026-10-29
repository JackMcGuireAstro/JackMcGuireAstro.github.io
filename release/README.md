# Release record

## What identifies a release

Each GitHub Pages deployment writes `release/manifest.json` (scripts/build_release_manifest.py,
a step of "Validate and deploy site"). It names the site code commit, the CTAS and
WorldsIndex data commits, each release's identifiers and checksums, the versioned source
universes, and the SHA-256 of the known-limitations statement and of every benchmark and
reproducibility file deployed with it. It is made from the files being deployed, so it
always describes the live site: https://jackmcguireastro.github.io/release/manifest.json

The versioned source universes themselves are release files:
- CTAS: `ctas/data/source-universe.json` (generated from the executable source contracts;
  artifact, contract-set and definition checksums) and `ctas/data/source-accounting.json`
  (the four denominators per source).
- WorldsIndex: `worldsindex/data/registry.json.gz` (42 declared sources) and
  `worldsindex/data/source-coverage.json` (the four denominators per source).

## DOI — documented, not executed

Nothing is published or registered at this stage (owner's decision of 2026-10-19). When that
changes, the steps are:
1. Choose the archive (Zenodo's GitHub integration is the usual route) and the licence text
   for data versus code.
2. Archive a tagged commit of this repository together with a dated copy of both data
   branches and the `release/manifest.json` of that deployment.
3. Add the DOI to CITATION.cff and codemeta.json in all three repositories, and to the
   manifest's `doi` field.
Until then `doi` is null and `doi_state` says why.

## GitHub Pages size

Measured 2026-10-29 on the published releases (ctas-data defb23a8, worldsindex-data
cf80c35d), as the deploy compresses them: about 0.75 GB, plus about 23 MB of Track 5 visitor
files, against GitHub Pages' 1 GB limit. CTAS dossier chunks are 281 MB gzip-compressed
(1.92 GB as JSON); other CTAS data 223 MB; WorldsIndex data 167 MB; the rest is site code,
images and QualQuest. CTAS grows with every event retained.

Proposed mitigation (not implemented): serve the CTAS dossier chunks from the `ctas-data`
branch through raw.githubusercontent.com (Track 0 measured HTTP Range and CORS working
there) instead of the Pages tree, keeping only the manifest on Pages; the chunk loader
already verifies every file's length and SHA-256 against the manifest, so the source of the
bytes does not change what is trusted. That would take the Pages tree to about 0.5 GB.
Cheaper partial steps: move the personal images out of the repository root, and exclude
`tests/` fixtures from the Pages artifact.
