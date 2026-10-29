#!/bin/bash
# Prepare the CTAS complete-catalog chunk files of a site tree about to be deployed.
#
# GitHub Pages limits a published site to 1 GB. The ~4,100 CTAS dossier roots and parts
# are 1.9 GB of JSON (281 MB gzipped), and the rest of the site is about 0.47 GB.
#
# Two modes:
#
#   CTAS_DATA_COMMIT=<40-hex sha> bash scripts/compress_ctas_chunks.sh [site-root]
#     The deploy (delivery batch, 2026-10-29). The chunk roots and parts are removed from
#     the tree; ctas/delivery.json names where the browser, the Python client and anyone
#     else reads them: the raw.githubusercontent.com address of exactly that ctas-data
#     commit, which is the commit the deployed release came from. The address is pinned by
#     commit, never by branch name, because the data branch is replaced wholesale on every
#     release; a GitHub commit stays readable at its SHA after the branch moves on. The
#     manifest (candidate-chunks/manifest.json) stays in the site, and ctas/chunk-loader.js
#     still verifies every file's byte length and SHA-256 against it, whatever the origin.
#     scripts/check_chunk_origin.py proves, before this step, that the origin serves the
#     release with CORS and byte ranges.
#
#   bash scripts/compress_ctas_chunks.sh [site-root]
#     Without the commit (local previews, and deployments of site code older than the
#     delivery batch, which the deploy fallback can choose): the chunk files stay in the tree,
#     gzipped (7.8x smaller), and ctas/delivery.json says "gzip", so the loader fetches
#     <name>.json.gz, decompresses it, and verifies the plain JSON as before.
#
# Only the deployed copy is changed either way: the release commit, its certificate and
# every test keep the plain JSON.
set -euo pipefail
ROOT=${1:-.}
DIR="$ROOT/ctas/data/candidate-chunks"
REPO=${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}
COMMIT=${CTAS_DATA_COMMIT:-}
[ -f "$DIR/manifest.json" ] || { echo "no CTAS chunk manifest under $DIR" >&2; exit 1; }
before=$(du -sk "$DIR" | cut -f1)
if [ -n "$COMMIT" ] && [ "${CTAS_CHUNK_ORIGIN:-raw}" != "pages" ]; then
  case "$COMMIT" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
    *) echo "CTAS_DATA_COMMIT must be a 40-digit lowercase hex commit, got '$COMMIT'" >&2; exit 1 ;;
  esac
  count=$(find "$DIR" -maxdepth 1 -type f \( -name '*.json' -o -name '*.json.gz' \) ! -name manifest.json | wc -l | tr -d ' ')
  find "$DIR" -maxdepth 1 -type f \( -name '*.json' -o -name '*.json.gz' \) ! -name manifest.json -delete
  left=$(find "$DIR" -maxdepth 1 -type f ! -name manifest.json | wc -l | tr -d ' ')
  [ "$left" -eq 0 ] || { echo "$left chunk files are still in the tree" >&2; exit 1; }
  cat >"$ROOT/ctas/delivery.json" <<JSON
{
  "schema": "ctas.delivery@1.0.0",
  "candidate_chunk_encoding": "gzip",
  "candidate_chunk_origin": {
    "base_url": "https://raw.githubusercontent.com/$REPO/$COMMIT/",
    "encoding": "identity",
    "branch": "ctas-data",
    "commit": "$COMMIT",
    "fallback_base_url": "https://raw.githubusercontent.com/$REPO/ctas-data/",
    "pages_copy": false,
    "note": "set by scripts/compress_ctas_chunks.sh for the GitHub Pages deployment: the dossier roots and parts are read from the release's data commit; candidate-chunks/manifest.json stays here and binds every file"
  }
}
JSON
  after=$(du -sk "$DIR" | cut -f1)
  echo "moved $count CTAS chunk files out of the site tree ($((before / 1024)) MB -> $((after / 1024)) MB); served from raw.githubusercontent.com/$REPO/${COMMIT:0:10}/"
else
  find "$DIR" -maxdepth 1 -type f -name '*.json' ! -name manifest.json -print0 \
    | xargs -0 -r -n 256 -P 4 gzip -n -9 --
  count=$(find "$DIR" -maxdepth 1 -type f -name '*.json.gz' | wc -l | tr -d ' ')
  left=$(find "$DIR" -maxdepth 1 -type f -name '*.json' ! -name manifest.json | wc -l | tr -d ' ')
  [ "$left" -eq 0 ] || { echo "$left chunk files were not compressed" >&2; exit 1; }
  cat >"$ROOT/ctas/delivery.json" <<'JSON'
{
  "schema": "ctas.delivery@1.0.0",
  "candidate_chunk_encoding": "gzip",
  "note": "set by scripts/compress_ctas_chunks.sh for the GitHub Pages deployment"
}
JSON
  after=$(du -sk "$DIR" | cut -f1)
  echo "compressed $count CTAS chunk files: $((before / 1024)) MB -> $((after / 1024)) MB"
fi
