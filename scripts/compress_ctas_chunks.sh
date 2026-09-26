#!/bin/bash
# Gzip the CTAS complete-catalog chunk files in a site tree about to be deployed.
#
# GitHub Pages limits a published site to 1 GB and the ~4,100 CTAS dossier roots and
# parts were ~580 MB of JSON (7.8x smaller gzipped). The deploy step replaces each
# ctas/data/candidate-chunks/<name>.json (except manifest.json) with <name>.json.gz.
# ctas/delivery.json is set to "gzip" so ctas/chunk-loader.js fetches the .gz form,
# decompresses it, and verifies the exact
# byte length and SHA-256 the manifest declares for the JSON, so the published
# contract is unchanged. Only the deployed copy is compressed: the release commit,
# its certificate and every test keep the plain JSON.
#
#   bash scripts/compress_ctas_chunks.sh [site-root]
set -euo pipefail
ROOT=${1:-.}
DIR="$ROOT/ctas/data/candidate-chunks"
[ -f "$DIR/manifest.json" ] || { echo "no CTAS chunk manifest under $DIR" >&2; exit 1; }
before=$(du -sk "$DIR" | cut -f1)
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
