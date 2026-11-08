#!/bin/bash
# The deploy workflow's live check (verify-live job), as a script so it can be tested.
#
# Compares what GitHub Pages serves after a deployment with what the validate job deployed:
# the WorldsIndex manifest and catalog index, the CTAS status and the largest CTAS dossier
# chunk, read the way the browser reads it (from the origin the live ctas/delivery.json names:
# the release's data commit on raw.githubusercontent.com, with CORS; or, for site code older
# than the delivery batch, the gzip copy on Pages). Pages propagates a deploy over a minute or
# two, file by file, so all four are compared together and retried.
#
# 2026-11-08: one deploy (run 42479503762) failed this check with nothing recorded about which
# file disagreed. Now every attempt names each mismatch with the HTTP status of the request,
# the last attempt's detail goes to $GITHUB_OUTPUT (detail=...) for the run report, and a chunk
# that the data origin refuses with HTTP 429 (rate limit) on every attempt while everything
# else matches is reported as "verified with a warning" rather than a failed deploy: the
# runner's shared address being rate-limited by raw.githubusercontent.com says nothing about
# what the site serves (the chunk's bytes are bound by the manifest the page checks anyway).
#
# Environment: PAGE_URL, WI_MANIFEST_SHA, WI_INDEX_SHA, CTAS_STATUS_SHA, CTAS_CHUNK_PATH,
# CTAS_CHUNK_SHA (required); WI_COMMIT, CTAS_COMMIT, WI_OBJECTS, WI_ROWS (for the summary);
# VERIFY_ATTEMPTS (default 20), VERIFY_SLEEP (default 20 s), VERIFY_RUN_ID (cache buster,
# default $GITHUB_RUN_ID), VERIFY_ORIGIN_PREFIX (default https://raw.githubusercontent.com/;
# tests point it at a local server).
set -uo pipefail
: "${PAGE_URL:?}" "${WI_MANIFEST_SHA:?}" "${WI_INDEX_SHA:?}" "${CTAS_STATUS_SHA:?}" "${CTAS_CHUNK_PATH:?}" "${CTAS_CHUNK_SHA:?}"
ATTEMPTS=${VERIFY_ATTEMPTS:-20}
PAUSE=${VERIFY_SLEEP:-20}
RUN=${VERIFY_RUN_ID:-${GITHUB_RUN_ID:-0}}
PREFIX=${VERIFY_ORIGIN_PREFIX:-https://raw.githubusercontent.com/}
SITE="${PAGE_URL%/}"
TMP=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/verify-live.XXXXXX") || exit 2
trap 'rm -rf "$TMP"' EXIT

# get <url> <out-file> [extra curl args...]: prints the HTTP status (000 when unreachable).
get() {
  local url=$1 out=$2; shift 2
  curl -sS -L -o "$out" -w '%{http_code}' -H 'Cache-Control: no-cache' "$@" "$url" 2>/dev/null || true
}
sha() { if [ -s "$1" ]; then sha256sum "$1" | cut -d' ' -f1; else echo missing; fi; }

chunk_origin() {
  local status
  status=$(get "$SITE/ctas/delivery.json?verify=$RUN" "$TMP/delivery.json")
  [ "$status" = 200 ] || { echo ""; return; }
  python3 - "$TMP/delivery.json" "$PREFIX" <<'PY' 2>/dev/null || echo ""
import json, sys
d = json.load(open(sys.argv[1]))
o = d.get("candidate_chunk_origin") or {}
base = str(o.get("base_url", ""))
print(base if d.get("schema") == "ctas.delivery@1.0.0" and base.startswith(sys.argv[2]) else "")
PY
}

# Sets chunk_status and writes the chunk's plain bytes to $TMP/chunk.
fetch_chunk() {
  local origin=$1
  rm -f "$TMP/chunk" "$TMP/chunk.gz" "$TMP/chunk-headers"
  if [ -n "$origin" ]; then
    chunk_status=$(get "${origin}${CTAS_CHUNK_PATH}" "$TMP/chunk" --compressed -D "$TMP/chunk-headers" -H "Origin: https://jackmcguireastro.github.io")
    # As the browser does: from the origin, with CORS; a missing CORS header is a mismatch.
    if [ "$chunk_status" = 200 ] && ! grep -qi '^access-control-allow-origin: \*' "$TMP/chunk-headers"; then
      chunk_status="200-no-cors"; rm -f "$TMP/chunk"
    fi
  else
    chunk_status=$(get "$SITE/${CTAS_CHUNK_PATH}.gz?verify=$RUN" "$TMP/chunk.gz")
    if [ "$chunk_status" = 200 ]; then gunzip -c "$TMP/chunk.gz" > "$TMP/chunk" 2>/dev/null || chunk_status="200-not-gzip"; fi
  fi
}

check_live() {
  local s
  mismatch=""
  s=$(get "$SITE/worldsindex/data/manifest.json?verify=$RUN" "$TMP/manifest")
  [ "$(sha "$TMP/manifest")" = "$WI_MANIFEST_SHA" ] || mismatch="$mismatch WorldsIndex-manifest(HTTP $s)"
  s=$(get "$SITE/worldsindex/data/catalog-index.json.gz?verify=$RUN" "$TMP/index")
  [ "$(sha "$TMP/index")" = "$WI_INDEX_SHA" ] || mismatch="$mismatch WorldsIndex-catalog-index(HTTP $s)"
  s=$(get "$SITE/ctas/data/status.json?verify=$RUN" "$TMP/status")
  [ "$(sha "$TMP/status")" = "$CTAS_STATUS_SHA" ] || mismatch="$mismatch CTAS-status(HTTP $s)"
  origin=$(chunk_origin)
  fetch_chunk "$origin"
  chunk_ok=0
  [ "$(sha "$TMP/chunk")" = "$CTAS_CHUNK_SHA" ] && chunk_ok=1
  [ "$chunk_ok" = 1 ] || mismatch="$mismatch CTAS-chunk(${CTAS_CHUNK_PATH##*/}, HTTP $chunk_status${origin:+ from the data origin})"
  [ -z "$mismatch" ]
}

out() { [ -n "${GITHUB_OUTPUT:-}" ] && printf '%s\n' "$1" >> "$GITHUB_OUTPUT"; return 0; }
summary() { echo "$1"; [ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "$1" >> "$GITHUB_STEP_SUMMARY"; return 0; }

only_rate_limited=1
attempt=0
while [ "$attempt" -lt "$ATTEMPTS" ]; do
  attempt=$((attempt + 1))
  if check_live; then
    summary "live site verified: WorldsIndex ${WI_COMMIT:0:10} (${WI_OBJECTS:-?} objects, ${WI_ROWS:-?} rows), CTAS ${CTAS_COMMIT:0:10} (status and chunk ${CTAS_CHUNK_PATH##*/} from ${origin:-the Pages gzip copy})"
    out "detail=verified on attempt $attempt"
    exit 0
  fi
  # Everything but the chunk matches and the data origin answered 429: remember, keep trying.
  case "$mismatch" in
    " CTAS-chunk("*", HTTP 429 from the data origin)") ;;
    *) only_rate_limited=0 ;;
  esac
  echo "attempt $attempt: not yet live:$mismatch; waiting for Pages to propagate"
  [ "$attempt" -lt "$ATTEMPTS" ] && sleep "$PAUSE"
done

if [ "$only_rate_limited" = 1 ]; then
  summary "live site verified with a warning: WorldsIndex and the CTAS status match; the data origin rate-limited (HTTP 429) every read of chunk ${CTAS_CHUNK_PATH##*/}, so its bytes could not be compared from this runner"
  out "detail=verified with a warning: chunk origin rate-limited (HTTP 429) on all $ATTEMPTS attempts"
  exit 0
fi
echo "the live site never matched the deployed release:$mismatch" >&2
out "detail=never matched after $ATTEMPTS attempts:$mismatch"
exit 1
