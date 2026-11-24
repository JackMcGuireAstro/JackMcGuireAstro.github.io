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
#   CHECK_TIMEOUT_SECONDS a time limit for each check's command (optional; ignored for a shell
#                       function and where `timeout` is missing). A check that runs out of
#                       time is stopped and recorded as failed, so an integrity check that
#                       hangs blocks like one that fails (2026-11-15: a static suite that ran
#                       out of memory hung a deploy for 48 minutes).
#   CHECK_UNCHANGED     products whose inputs (site code and data release) are identical to
#                       the live deployment's, comma-separated: ctas, worldsindex (the deploy
#                       sets it from the live release/manifest.json). A check run under
#                       CHECK_PRODUCT=ctas|worldsindex|both is then recorded as skipped: the
#                       same checks on the same bytes passed when that deployment went live
#                       (2026-11-24: deploys took 20-51 min, most of it re-checking the
#                       product that had not changed). CHECK_UNCHANGED_WHY names the deployment.
#   CHECK_RETRY_ADVISORY=1 run a failed ADVISORY check once more before recording it, and
#                       record a pass when the second run passes (the output keeps both runs).
#                       Browser suites flake on timing (2026-11-24: one advisory browser check
#                       opened 81 issues in two weeks); a blocking check is never retried.

unchanged_product() {  # unchanged_product ctas|worldsindex|both: is it in CHECK_UNCHANGED?
  case "$1" in
    both) unchanged_product ctas && unchanged_product worldsindex ;;
    *) case ",${CHECK_UNCHANGED:-}," in *",$1,"*) return 0 ;; esac; return 1 ;;
  esac
}

check() {
  local id=$1 out rc=0 started arg
  shift
  mkdir -p "$CHECK_DIR"
  out="$CHECK_DIR/$id.log"
  if [ -n "${CHECK_PRODUCT:-}" ] && unchanged_product "$CHECK_PRODUCT"; then
    record_check "$id" 0 "" --skip "$CHECK_PRODUCT unchanged since the live deployment${CHECK_UNCHANGED_WHY:+ ($CHECK_UNCHANGED_WHY)}: same code, same data, already checked"
    return $?
  fi
  if [ "${CHECK_ONLY_BLOCKING:-0}" = "1" ] && ! python3 "$RELEASE_CHECKS" blocking --id "$id" 2>/dev/null; then
    record_check "$id" 0 "" --skip "advisory: not run for a fallback candidate"
    return $?
  fi
  if [ "${CHECK_SKIP_MISSING:-0}" = "1" ]; then
    for arg in "$@"; do
      case "$arg" in
        scripts/*|ctas/*|worldsindex/*|tonight/*|tools/*|research/*)
          if [ ! -e "$arg" ]; then
            record_check "$id" 0 "" --skip "$arg is absent from this tree"
            return $?
          fi ;;
      esac
    done
  fi
  started=$(date +%s)
  if [ -n "${CHECK_TIMEOUT_SECONDS:-}" ] && command -v timeout >/dev/null 2>&1 && ! declare -F "$1" >/dev/null; then
    # --foreground keeps the check in the caller's process group, so the cloud runner's
    # guard (scripts/ctas_cloud_run.sh run_guarded) still stops it with the publisher.
    timeout --foreground --kill-after=30 "$CHECK_TIMEOUT_SECONDS" "$@" >"$out" 2>&1 || rc=$?
    case "$rc" in
      124) printf '\nTIMEOUT  %s did not finish within %s seconds; counted as a failure\n' \
             "$id" "$CHECK_TIMEOUT_SECONDS" >>"$out" ;;
      137) printf '\nKILLED  %s was killed (time limit of %s seconds, or memory); counted as a failure\n' \
             "$id" "$CHECK_TIMEOUT_SECONDS" >>"$out" ;;
    esac
  else
    "$@" >"$out" 2>&1 || rc=$?
  fi
  if [ "$rc" -ne 0 ] && [ "${CHECK_RETRY_ADVISORY:-0}" = "1" ] && ! python3 "$RELEASE_CHECKS" blocking --id "$id" 2>/dev/null; then
    printf '\nRETRY  %s failed (exit %s); advisory, so it runs once more\n' "$id" "$rc" >>"$out"
    rc=0
    if [ -n "${CHECK_TIMEOUT_SECONDS:-}" ] && command -v timeout >/dev/null 2>&1 && ! declare -F "$1" >/dev/null; then
      timeout --foreground --kill-after=30 "$CHECK_TIMEOUT_SECONDS" "$@" >>"$out" 2>&1 || rc=$?
    else
      "$@" >>"$out" 2>&1 || rc=$?
    fi
    if [ "$rc" -eq 0 ]; then printf '\nRETRY  %s passed on the second run (a flake, not a defect)\n' "$id" >>"$out"
    else printf '\nRETRY  %s failed twice (exit %s)\n' "$id" "$rc" >>"$out"; fi
  fi
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
