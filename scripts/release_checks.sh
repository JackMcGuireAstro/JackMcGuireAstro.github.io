# shellcheck shell=bash
# Classified release checks for bash: sourced by scripts/publish_ctas.sh,
# scripts/publish_worldsindex.sh and scripts/deploy_checks.sh.
#
#   check <id> <command...>    run one check, keep its output, classify the result
#
# The result is classified by scripts/release_checks.py from scripts/check-classes.json:
# `check` returns 0 when the check passed or failed without blocking (advisory, or an
# integrity check still in watch mode; a WARN line says so) and 3 when the failure blocks
# (always_block, or a promoted integrity check). Anything that goes wrong while classifying
# also returns 3, so a broken classifier can never wave a release through.
#
# The caller sets:
#   RELEASE_CHECKS      path to scripts/release_checks.py
#   CHECK_SCOPE         ctas-publish | worldsindex-publish | deploy
#   CHECK_RESULTS       JSON Lines file the results are appended to
#   CHECK_DIR           folder for each check's output (<id>.log)
#   CHECK_HISTORY       check-history.json file(s), os.pathsep-separated (optional)
#   CHECK_TREE          the checkout the checks run in (default: the current directory); a
#                       per-schema check hashes its schema files there
#   CHECK_LOG           a log that also receives every output and verdict (optional)
#   CHECK_ECHO=1        print each check's output (last CHECK_ECHO_LINES lines, default 200)
#   CHECK_SKIP_MISSING=1  record a check as skipped when a file it names is absent (the
#                       deploy's runs against older candidate trees, which may predate a test)
#   CHECK_ONLY_BLOCKING=1 record an advisory check as skipped without running it (the deploy's
#                       fallback candidates: an advisory result cannot change which one is chosen)

check() {
  local id=$1 out rc=0 started arg
  shift
  mkdir -p "$CHECK_DIR"
  out="$CHECK_DIR/$id.log"
  if [ "${CHECK_ONLY_BLOCKING:-0}" = "1" ] && ! python3 "$RELEASE_CHECKS" blocking --id "$id" 2>/dev/null; then
    record_check "$id" 0 "" --skip "advisory: not run for a fallback candidate"
    return $?
  fi
  if [ "${CHECK_SKIP_MISSING:-0}" = "1" ]; then
    for arg in "$@"; do
      case "$arg" in
        scripts/*|ctas/*|worldsindex/*|tonight/*|tools/*|research/*|qualquest/*)
          if [ ! -e "$arg" ]; then
            record_check "$id" 0 "" --skip "$arg is absent from this tree"
            return $?
          fi ;;
      esac
    done
  fi
  started=$(date +%s)
  "$@" >"$out" 2>&1 || rc=$?
  [ -z "${CHECK_LOG:-}" ] || cat "$out" >>"$CHECK_LOG"
  [ "${CHECK_ECHO:-0}" != "1" ] || tail -n "${CHECK_ECHO_LINES:-200}" "$out"
  record_check "$id" "$rc" "$out" --seconds "$(( $(date +%s) - started ))"
}

# record_check <id> <exit code> <output file or ""> [release_checks.py record options...]
record_check() {
  local id=$1 rc=$2 out=$3 verdict status=0
  shift 3
  local args=(record --scope "$CHECK_SCOPE" --id "$id" --exit-code "$rc" --results "$CHECK_RESULTS" --tree "${CHECK_TREE:-$PWD}")
  [ -z "$out" ] || args+=(--output "$out")
  [ -z "${CHECK_HISTORY:-}" ] || args+=(--history "$CHECK_HISTORY")
  verdict=$(python3 "$RELEASE_CHECKS" "${args[@]}" "$@" 2>&1) || status=$?
  [ -z "${CHECK_LOG:-}" ] || printf '%s\n' "$verdict" >>"$CHECK_LOG"
  case "$verdict" in
    *": passed") [ "${CHECK_ECHO:-0}" != "1" ] || printf '%s\n' "$verdict" ;;
    *) printf '%s\n' "$verdict" ;;
  esac
  case "$status" in
    0) return 0 ;;
    3) return 3 ;;
    *) printf 'FAIL  check %s could not be classified (release_checks.py exit %s); treated as blocking\n' "$id" "$status"
       return 3 ;;
  esac
}
