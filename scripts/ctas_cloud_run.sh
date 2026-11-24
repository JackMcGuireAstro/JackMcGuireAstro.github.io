#!/bin/bash
# One CTAS cloud cycle, run by .github/workflows/ctas-cloud.yml (nothing on the Mac).
#
#   1. restore the collector's database and fixed inputs from the private state repo
#   2. start the collector (`supernova-ops api`, the same program the Mac ran) and let it
#      gather new alerts for COLLECT_MINUTES
#   3. stop the collector cleanly, then publish a release with scripts/publish_ctas.sh
#      (since 2026-11-12: the collector and the exporter together exceeded the runner's
#      16 GB and the kernel killed the job, with no report, for 10 hours; the publisher
#      still freezes its own snapshot, so nothing about the release changes)
#   4. save the database back to the state repo
#   5. start the site deployment when a release was published
#
# Runner guard (2026-11-14: every other cycle ended with "The hosted runner lost
# communication with the server" while the release was built, with no report and no log
# artifact). The runner now:
#   - samples memory, swap, disk and the three largest processes every 30 s (job log and
#     logs/resources.log) and writes one "phase <name> took Ns (peaks ...)" line per phase to
#     the run log, so the run report carries them; under pressure the latest samples are also
#     pushed to the run-reports branch (reports/ctas-cloud/telemetry-latest.txt), which
#     survives a lost runner;
#   - adds swap on the runner's spare disk, keeps the runner's own agent out of the kernel's
#     OOM choice and makes the publisher its first choice instead;
#   - runs the publisher under a memory and disk ceiling: it is stopped cleanly (process
#     group TERM, then KILL) when memory plus swap headroom or free disk runs out, or the
#     machine thrashes; the run then says "memory ceiling" (or "disk ceiling"), saves the
#     state and pushes its report;
#   - refuses to start the release when free disk is below what it needs;
#   - hands the publisher the stopped collector's database itself (read-only, checkpointed)
#     instead of a second 6 GB copy, and runs two light-curve workers unless told otherwise;
#   - reports the earlier runs that died without a report (lost runner) on the run-reports
#     branch, as "previous run died: lost runner".
#
# Resilience (RESILIENCE.md): every release check is classified (scripts/check-classes.json).
# When one blocks with the current site code (publisher exit 3) and that code differs from
# the commit that produced the currently published release, the release step is retried
# ONCE with that commit's code on the same frozen database, if scripts/release_checks.py
# decide allows it; the marker then carries code_fallback and a "CTAS published with
# fallback code" issue is opened or updated. Never more than one fallback attempt per run;
# the state is saved exactly as before either way. Each run syncs the check-warning,
# fallback-active and release-blocked issues, and a failed or fallback run pushes a
# sanitized report to the run-reports branch.
#
# Environment (set by the workflow):
#   SITE              this repository's checkout (main)
#   BACKEND           the collector's code (checkout of the private state repo)
#   STATE_TOKEN       token for the private state repo (read/write contents)
#   DEPLOY_TOKEN      this repository's workflow token (actions: write)
#   COLLECT_MINUTES   minutes of collection before publishing (default 15)
#   DRY_RUN           1 = collect and build a release but push nothing and save nothing
#   CHAIN             1 (default) = start the next cycle when this one saved its database
#   WORK              scratch folder for logs (default $RUNNER_TEMP or /tmp)
set -uo pipefail

SITE=${SITE:?SITE}
BACKEND=${BACKEND:?BACKEND}
COLLECT_MINUTES=${COLLECT_MINUTES:-15}
DRY_RUN=${DRY_RUN:-0}
WORK=${WORK:-${RUNNER_TEMP:-/tmp}/ctas-cloud}
LOGS="$WORK/logs"
DATA="$BACKEND/data"
PORT=${SOC_PORT:-8787}
mkdir -p "$LOGS"

say() { printf '%s  %s\n' "$(date -u '+%H:%M:%S')" "$*" | tee -a "$LOGS/runner.log"; }
fail() { say "FAIL  $*"; stop_collector; exit 1; }
# shellcheck source=code_fallback.sh
. "$SITE/scripts/code_fallback.sh"
REPO="${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}"
MAIN_COMMIT=$(git -C "$SITE" rev-parse HEAD 2>/dev/null || true)
CHECKS_MAIN="$WORK/checks-main.jsonl"
CHECKS_FALLBACK="$WORK/checks-fallback.jsonl"
DECISION="$WORK/decision.json"
FROZEN="$WORK/frozen-main.db"
FALLBACK_USED=0
RELEASE_COMMIT=""
rm -f -- "$CHECKS_MAIN" "$CHECKS_FALLBACK" "$DECISION" "$FROZEN"
# Every secret-bearing variable the CTAS workflow sets (scripts/run_report.py
# SECRET_ENV_NAMES; scripts/test_run_report.py keeps the list complete), removed from
# everything that becomes public (issues, reports), and the collector's settings file.
SECRET_ENV="STATE_TOKEN DEPLOY_TOKEN GH_TOKEN PUSH_TOKEN BACKEND_ENV AAVSO_AID_API_TOKEN_OVERRIDE"

FALLBACK_SCOPE=ctas-publish
FALLBACK_WORKFLOW=ctas-cloud.yml
decide() { fallback_decide "$@"; }

# The issues and the run report; a failure of either is reported, never fatal.
ISSUES_SYNCED=0
sync_issues() {
  [ "$DRY_RUN" != "1" ] && [ "$ISSUES_SYNCED" -eq 0 ] || return 0
  ISSUES_SYNCED=1
  GH_TOKEN="$DEPLOY_TOKEN" CHECK_SECRET_ENV="$SECRET_ENV" CHECK_SECRET_FILE="$BACKEND/.env" python3 "$SITE/scripts/release_issues.py" sync --scope ctas-publish \
    --results "$CHECKS_MAIN" --decision "$DECISION" --repo "$REPO" --secret-env "$SECRET_ENV" \
    --secret-file "$BACKEND/.env" 2>&1 | sed 's/^/issues: /' || true
}
push_report() {  # push_report <failed|fallback|blocked>
  [ "$DRY_RUN" != "1" ] || return 0
  python3 "$SITE/scripts/run_report.py" build --workflow ctas-cloud.yml --outcome "$1" --out "$WORK/run-report.json" \
    --commit "$MAIN_COMMIT" --log "$LOGS/runner.log" --log "$LOGS/publish/publish.log" --log "$LOGS/publish-fallback/publish.log" \
    --results "$CHECKS_MAIN" --results "$CHECKS_FALLBACK" --decision "$DECISION" \
    --secret-env "$SECRET_ENV" --secret-file "$BACKEND/.env" \
    && python3 "$SITE/scripts/run_report.py" push --report "$WORK/run-report.json" --repo-dir "$SITE" \
    || echo "WARN  could not push the run report"
}

COLLECTOR_PID=""
stop_collector() {
  [ -n "$COLLECTOR_PID" ] || return 0
  kill -0 "$COLLECTOR_PID" 2>/dev/null || { COLLECTOR_PID=""; return 0; }
  say "stopping the collector"
  kill -INT "$COLLECTOR_PID" 2>/dev/null
  for _ in $(seq 1 180); do kill -0 "$COLLECTOR_PID" 2>/dev/null || break; sleep 1; done
  if kill -0 "$COLLECTOR_PID" 2>/dev/null; then
    say "collector did not stop within 3 minutes; ending it (SQLite keeps the last committed state)"
    kill -KILL "$COLLECTOR_PID" 2>/dev/null
    sleep 2
  fi
  COLLECTOR_PID=""
}

# ------------------------------------------------------------------ runner guard
# Telemetry, the memory/disk ceiling around the publisher, swap, lost-runner reports.
# Nothing here may stop a run by itself: every helper reports and carries on.
RES_LOG="$LOGS/resources.log"            # uploaded with the publish-log artifact
RES_TSV="$WORK/resources.tsv"            # epoch phase used avail swapused root_free work_free headroom (MB)
PHASES_TSV="$WORK/phases.tsv"            # phase start_epoch
PHASE_FILE="$WORK/phase"
GUARD_HIT="$WORK/guard-hit"              # why the ceiling stopped the publisher
TELEMETRY_PATH="reports/ctas-cloud/telemetry-latest.txt"
MEM_FLOOR_MB=${CTAS_MEM_FLOOR_MB:-1536}  # stop the publisher below this RAM+swap headroom
MEM_PSI_FULL=${CTAS_MEM_PSI_FULL:-70}    # ... or when every task stalls on memory this % of the time
MEM_PSI_SAMPLES=${CTAS_MEM_PSI_SAMPLES:-12}  # ... for this many checks in a row (a minute)
GUARD_SECONDS=${CTAS_GUARD_SECONDS:-5}        # how often the ceiling is checked
DISK_FLOOR_MB=${CTAS_DISK_FLOOR_MB:-768}     # ... or below this free disk under the site or WORK
PUBLISH_MIN_FREE_MB=${CTAS_PUBLISH_MIN_FREE_MB:-6144}  # refuse to start the release below this
SWAP_GB=${CTAS_SWAP_GB:-8}
SAMPLE_SECONDS=${CTAS_SAMPLE_SECONDS:-30}
TELEMETRY_PUSH_SECONDS=${CTAS_TELEMETRY_PUSH_SECONDS:-300}  # publish phase: push the samples this often
# Two light-curve fit workers unless the repository variable says otherwise (the workflow
# passes an empty string when it is unset); the default was one per CPU, up to four.
LIGHTCURVE_WORKERS_SET=${CTAS_LIGHTCURVE_WORKERS:+1}
export CTAS_LIGHTCURVE_WORKERS="${CTAS_LIGHTCURVE_WORKERS:-2}"
rm -f -- "$RES_TSV" "$PHASES_TSV" "$GUARD_HIT"

free_mb() { df -Pm -- "$1" 2>/dev/null | awk 'NR==2 {print $4+0}'; }
# The memory limit of this script's control group, when it has one below the machine's
# (cgroup v2 memory.max, v1 memory.limit_in_bytes): headroom there is limit minus usage
# without the file cache (a 6 GB database read once fills it; the kernel reclaims it).
# Empty when there is no such limit (a hosted runner's job).
cg_headroom_mb() {
  local path dir limit usage cache
  path=$(awk -F: '$1 == "0" {print $3}' /proc/self/cgroup 2>/dev/null)
  dir="/sys/fs/cgroup${path}"
  if [ -n "$path" ] && [ -r "$dir/memory.max" ]; then
    limit=$(cat "$dir/memory.max"); usage=$(cat "$dir/memory.current" 2>/dev/null)
    cache=$(awk '$1 == "file" {print $2}' "$dir/memory.stat" 2>/dev/null)
  else
    path=$(awk -F: '$2 ~ /(^|,)memory(,|$)/ {print $3}' /proc/self/cgroup 2>/dev/null)
    dir="/sys/fs/cgroup/memory${path}"
    [ -n "$path" ] && [ -r "$dir/memory.limit_in_bytes" ] || return 0
    limit=$(cat "$dir/memory.limit_in_bytes"); usage=$(cat "$dir/memory.usage_in_bytes" 2>/dev/null)
    cache=$(awk '$1 == "total_cache" {print $2}' "$dir/memory.stat" 2>/dev/null)
  fi
  case "$limit" in ''|max|*[!0-9]*) return 0 ;; esac
  [ "${#limit}" -lt 19 ] || return 0   # "unlimited" in v1 is a 19-digit number
  awk -v l="$limit" -v u="${usage:-0}" -v i="${cache:-0}" 'BEGIN {printf "%d\n", (l - (u - i)) / 1048576}'
}
mem_line() {  # used avail swap_used swap_free headroom (MB); headroom is the smaller of the
  # machine's (available + free swap) and the control group's
  local cg
  cg=$(cg_headroom_mb)
  awk -v cg="$cg" '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} /^SwapTotal:/{st=$2} /^SwapFree:/{sf=$2}
       END {h = (a + sf) / 1024; if (cg != "" && cg + 0 < h) h = cg + 0
            printf "%d %d %d %d %d\n", (t-a)/1024, a/1024, (st-sf)/1024, sf/1024, h}' /proc/meminfo 2>/dev/null
}
psi_full() { awk '/^full/ {for (i = 1; i <= NF; i++) if ($i ~ /^avg10=/) {sub("avg10=", "", $i); printf "%d\n", $i}}' /proc/pressure/memory 2>/dev/null; }
set_phase() { printf '%s\n' "$1" >"$PHASE_FILE"; printf '%s %s\n' "$1" "$(date +%s)" >>"$PHASES_TSV"; }

push_telemetry() {  # the newest samples on the run-reports branch: they survive a lost runner
  [ "$DRY_RUN" != "1" ] && [ -n "${GITHUB_RUN_ID:-}" ] || return 0
  { printf 'run %s of ctas-cloud.yml, attempt %s, pushed %s, phase %s\n' "$GITHUB_RUN_ID" "${GITHUB_RUN_ATTEMPT:-1}" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(cat "$PHASE_FILE" 2>/dev/null)"
    tail -40 "$RES_LOG" 2>/dev/null; } >"$WORK/telemetry-latest.txt"
  timeout 120 python3 "$SITE/scripts/run_report.py" push --repo-dir "$SITE" --extra "$TELEMETRY_PATH=$WORK/telemetry-latest.txt" \
    --secret-env "$SECRET_ENV" --secret-file "$BACKEND/.env" >/dev/null 2>&1 || true
}

sampler() {  # every SAMPLE_SECONDS: one line to the job log and resources.log
  local last_push=0 mem used avail swap swapfree headroom root work mnt top psi phase now line
  while :; do
    now=$(date +%s)
    read -r used avail swap swapfree headroom <<<"$(mem_line)"
    root=$(free_mb /); work=$(free_mb "$WORK"); mnt=$(free_mb /mnt)
    psi=$(psi_full)
    phase=$(cat "$PHASE_FILE" 2>/dev/null || echo start)
    top=$(ps -eo rss=,comm= --sort=-rss 2>/dev/null | head -3 | awk '{printf "%s%s %.1fG", (NR > 1 ? ", " : ""), $2, $1/1048576}')
    line=$(printf '%s  res  %-9s mem used %.1fG avail %.1fG swap %.1fG headroom %.1fG | free / %.1fG work %.1fG%s | psi %s%% | top %s' \
      "$(date -u '+%H:%M:%S')" "$phase" "$(awk -v v="${used:-0}" 'BEGIN{print v/1024}')" "$(awk -v v="${avail:-0}" 'BEGIN{print v/1024}')" \
      "$(awk -v v="${swap:-0}" 'BEGIN{print v/1024}')" "$(awk -v v="${headroom:-0}" 'BEGIN{print v/1024}')" "$(awk -v v="${root:-0}" 'BEGIN{print v/1024}')" \
      "$(awk -v v="${work:-0}" 'BEGIN{print v/1024}')" "${mnt:+ /mnt $(awk -v v="$mnt" 'BEGIN{printf "%.1fG", v/1024}')}" "${psi:--}" "$top")
    printf '%s\n' "$line" | tee -a "$RES_LOG"
    printf '%s %s %s %s %s %s %s %s\n' "$now" "$phase" "${used:-0}" "${avail:-0}" "${swap:-0}" "${root:-0}" "${work:-0}" "${headroom:-0}" >>"$RES_TSV"
    # Push the newest samples to the run-reports branch so the next run can tell what this
    # one died of: every TELEMETRY_PUSH_SECONDS (5 min) while the release is being built,
    # the phase every lost runner so far died in (2026-11-24: 15 deaths in four days, none
    # under enough pressure for the old rule, so none left a trace), and under pressure
    # (headroom under 4 GB or under 3 GB of disk) in any phase. One push is a few seconds
    # and a few KB: about ten pushes per cycle.
    if { [ "$phase" = publish ] && [ $((now - last_push)) -ge "$TELEMETRY_PUSH_SECONDS" ]; } \
       || { { [ "${headroom:-99999}" -lt 4096 ] || [ "${root:-99999}" -lt 3072 ] || [ "${work:-99999}" -lt 3072 ]; } \
            && [ $((now - last_push)) -ge "$TELEMETRY_PUSH_SECONDS" ]; }; then
      last_push=$now
      push_telemetry
    fi
    sleep "$SAMPLE_SECONDS"
  done
}
SAMPLER_PID=""
start_sampler() { sampler 2>/dev/null & SAMPLER_PID=$!; }
stop_sampler() {
  [ -n "$SAMPLER_PID" ] || return 0
  pkill -P "$SAMPLER_PID" 2>/dev/null   # its sleep
  kill "$SAMPLER_PID" 2>/dev/null
  wait "$SAMPLER_PID" 2>/dev/null
  SAMPLER_PID=""
}

SUMMARY_DONE=0
resource_summary() {  # one "phase <name> took Ns (...)" line per phase (scripts/run_report.py keeps them)
  [ "$SUMMARY_DONE" -eq 0 ] && [ -s "$PHASES_TSV" ] || return 0
  SUMMARY_DONE=1
  local total
  total=$(awk '/^MemTotal:/ {printf "%.1f", $2/1048576}' /proc/meminfo 2>/dev/null)
  awk -v end="$(date +%s)" -v total="${total:-?}" '
    FNR == NR { order[++n] = $1; start[$1] = $2; next }
    { p = $2; c[p]++
      if (!(p in used) || $3 > used[p]) used[p] = $3
      if (!(p in avail) || $4 < avail[p]) avail[p] = $4
      if (!(p in swap) || $5 > swap[p]) swap[p] = $5
      if (!(p in root) || $6 < root[p]) root[p] = $6
      if (!(p in work) || $7 < work[p]) work[p] = $7
      if (!(p in head) || $8 < head[p]) head[p] = $8 }
    END { for (i = 1; i <= n; i++) { p = order[i]; stop = (i < n ? start[order[i + 1]] : end)
            if (p in c) printf "phase  %s took %ds (peak memory used %.1f of %s GiB, min available %.1f GiB, peak swap %.1f GiB, min headroom %.1f GiB, min free disk / %.1f GiB, work %.1f GiB; %d samples)\n", p, stop - start[p], used[p] / 1024, total, avail[p] / 1024, swap[p] / 1024, head[p] / 1024, root[p] / 1024, work[p] / 1024, c[p]
            else printf "phase  %s took %ds (no sample)\n", p, stop - start[p] } }' "$PHASES_TSV" "$RES_TSV" 2>/dev/null \
    | while IFS= read -r line; do say "$line"; done
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    { printf '\n## Runner resources\n\n```\n'; grep -E '  phase  ' "$LOGS/runner.log" | tail -12; printf '```\n'; } >>"$GITHUB_STEP_SUMMARY" 2>/dev/null || true
  fi
}

add_swap() {  # more swap on the runner's spare disk (/mnt), never on the system disk
  # 2026-11-23: swap on the system disk (bc4298e4, for runners without a separate /mnt) was
  # followed by 15 "hosted runner lost communication" deaths in four days (one in the four
  # days before): paging on the disk the runner agent and the database share starves the
  # agent. Reverted. A runner without /mnt swap instead builds the release with one
  # light-curve worker (lighter, slower), unless CTAS_LIGHTCURVE_WORKERS was set by hand,
  # and the memory guard still stops it cleanly if that is not enough.
  [ "${SWAP_GB:-0}" -gt 0 ] 2>/dev/null || return 0
  command -v sudo >/dev/null && sudo -n true 2>/dev/null || { say "swap: no sudo; unchanged"; NO_EXTRA_SWAP=1; return 0; }
  local file=/mnt/ctas-swap free
  [ -d /mnt ] && [ "$(stat -c %d /mnt)" != "$(stat -c %d /)" ] || { say "swap: /mnt is not a separate disk; unchanged"; NO_EXTRA_SWAP=1; return 0; }
  free=$(free_mb /mnt)
  [ "${free:-0}" -ge $(( (SWAP_GB + 4) * 1024 )) ] || { say "swap: only ${free:-0} MB free on /mnt; unchanged"; NO_EXTRA_SWAP=1; return 0; }
  if sudo -n fallocate -l "${SWAP_GB}G" "$file" && sudo -n chmod 600 "$file" && sudo -n mkswap -q "$file" >/dev/null && sudo -n swapon "$file"; then
    say "swap: added ${SWAP_GB} GB on /mnt ($(awk '/^SwapTotal:/ {printf "%.1f", $2/1048576}' /proc/meminfo) GB in all)"
  else
    sudo -n rm -f "$file" 2>/dev/null
    say "swap: could not add ${SWAP_GB} GB; unchanged"
    NO_EXTRA_SWAP=1
  fi
}
protect_agent() {  # the runner's own processes are never the kernel's OOM choice
  command -v sudo >/dev/null && sudo -n true 2>/dev/null || return 0
  local pid n=0
  for pid in $(pgrep -f 'Runner\.(Listener|Worker)|runsvc|provjobd' 2>/dev/null); do
    printf '%s\n' -1000 | sudo -n tee "/proc/$pid/oom_score_adj" >/dev/null 2>&1 && n=$((n + 1))
  done
  say "runner agent: $n process(es) excluded from the OOM killer"
}

# run_guarded <label> <output file> <command ...>: the command in its own process group
# (OOM score raised, so the kernel picks it before anything else), checked every GUARD_SECONDS.
# It is stopped (TERM to the group, KILL after 60 s) when RAM+swap headroom falls under
# MEM_FLOOR_MB, when the machine thrashes (memory PSI "full" over MEM_PSI_FULL% for
# MEM_PSI_SAMPLES samples), or when free disk under the site or WORK falls under
# DISK_FLOOR_MB; the reason goes to GUARD_HIT. Returns the command's exit status.
run_guarded() {
  local label=$1 out=$2 pidfile="$WORK/guard-$1.pid" pid pgid status thrash=0 headroom root work psi reason=""
  shift 2
  rm -f -- "$pidfile"
  setsid -w bash -c 'echo $$ >"$0"; echo 900 >/proc/self/oom_score_adj 2>/dev/null; exec "$@"' "$pidfile" "$@" >"$out" 2>&1 &
  pid=$!
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$pidfile" ] && break; sleep 0.2; done
  pgid=$(cat "$pidfile" 2>/dev/null)
  while kill -0 "$pid" 2>/dev/null; do
    for _ in $(seq 1 "$GUARD_SECONDS"); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null || break
    read -r _ _ _ _ headroom <<<"$(mem_line)"
    root=$(free_mb "$SITE"); work=$(free_mb "$WORK"); psi=$(psi_full)
    if [ "${psi:-0}" -ge "$MEM_PSI_FULL" ]; then thrash=$((thrash + 1)); else thrash=0; fi
    if [ "${headroom:-99999}" -lt "$MEM_FLOOR_MB" ]; then
      reason="memory ceiling: ${headroom} MB of memory and swap left (floor ${MEM_FLOOR_MB} MB)"
    elif [ "$thrash" -ge "$MEM_PSI_SAMPLES" ]; then
      reason="memory ceiling: the machine was thrashing (memory pressure ${psi}% for $((thrash * GUARD_SECONDS)) s)"
    elif [ "${root:-99999}" -lt "$DISK_FLOOR_MB" ] || [ "${work:-99999}" -lt "$DISK_FLOOR_MB" ]; then
      reason="disk ceiling: ${root} MB free under the site, ${work} MB under WORK (floor ${DISK_FLOOR_MB} MB)"
    fi
    [ -n "$reason" ] || continue
    printf '%s (%s)\n' "$reason" "$label" >"$GUARD_HIT"
    say "FAIL  $reason; stopping the $label cleanly" >&2
    printf '%s\n' "$(cat "$RES_LOG" 2>/dev/null | tail -1)" >&2
    [ -n "$pgid" ] && kill -TERM -- "-$pgid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then
      [ -n "$pgid" ] && kill -KILL -- "-$pgid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
    fi
    break
  done
  wait "$pid"
  status=$?
  # whatever the group left behind (a worker that ignored TERM)
  [ -n "$pgid" ] && kill -KILL -- "-$pgid" 2>/dev/null
  rm -f -- "$pidfile"
  return "$status"
}

# Earlier runs of this workflow that failed without a report on the run-reports branch
# (the runner was lost, or the job died before this script could report) get one now,
# noted "previous run died: lost runner" when GitHub's annotation says so. Reports, never fatal.
report_lost_runs() {
  [ "$DRY_RUN" != "1" ] && [ -n "${GITHUB_RUN_ID:-}" ] && [ -n "${DEPLOY_TOKEN:-}" ] || return 0
  local rows id attempt sha created url why note log since telemetry
  since=$(date -u -d '-24 hours' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo 1970-01-01T00:00:00Z)
  rows=$(GH_TOKEN="$DEPLOY_TOKEN" timeout 60 gh api "repos/$REPO/actions/workflows/ctas-cloud.yml/runs?status=completed&per_page=12" \
    --jq ".workflow_runs[] | select(.conclusion == \"failure\" and .id != ${GITHUB_RUN_ID} and .created_at >= \"$since\") | [.id, .run_attempt, .head_sha, .created_at, .html_url] | @tsv" 2>/dev/null) \
    || { say "WARN  could not read the earlier runs; lost-runner check skipped"; return 0; }
  printf '%s\n' "$rows" | head -3 | while IFS=$'\t' read -r id attempt sha created url; do
    [ -n "$id" ] || continue
    GH_TOKEN="$DEPLOY_TOKEN" timeout 30 gh api "repos/$REPO/contents/reports/ctas-cloud/$id.json?ref=run-reports" --silent >/dev/null 2>&1 && continue
    why=$(GH_TOKEN="$DEPLOY_TOKEN" timeout 30 gh api "repos/$REPO/actions/runs/$id/jobs" --jq '.jobs[] | select(.conclusion != "success") | .id' 2>/dev/null \
      | while read -r job; do GH_TOKEN="$DEPLOY_TOKEN" timeout 30 gh api "repos/$REPO/check-runs/$job/annotations" --jq '.[].message' 2>/dev/null; done \
      | grep -m1 -iE 'lost communication|runner has received a shutdown signal|shutdown signal' | cut -c1-160)
    if [ -n "$why" ]; then note="previous run died: lost runner"; else note="previous run died without a report"; fi
    log="$WORK/lost-$id.log"
    {
      printf 'FAIL  %s (run %s, started %s; detected by run %s)\n' "$note" "$id" "$created" "$GITHUB_RUN_ID"
      [ -n "$why" ] && printf 'FAIL  GitHub: %s\n' "$why"
      # the dead run's own last samples, when it pushed them under pressure
      telemetry=$(GH_TOKEN="$DEPLOY_TOKEN" timeout 30 gh api "repos/$REPO/contents/$TELEMETRY_PATH?ref=run-reports" \
        -H 'Accept: application/vnd.github.raw' 2>/dev/null)
      if printf '%s\n' "$telemetry" | head -1 | grep -q "^run $id "; then
        printf '%s\n' "$telemetry" | grep '  res  ' | tail -4 | sed 's/^/WARN  last telemetry of the dead run: /'
      else
        printf 'WARN  no telemetry of the dead run was pushed (it died outside the release build, or within five minutes of its last push)\n'
      fi
    } >"$log"
    say "WARN  $note: $url${why:+ ($why)}"
    if GITHUB_RUN_ID="$id" GITHUB_RUN_ATTEMPT="$attempt" python3 "$SITE/scripts/run_report.py" build --workflow ctas-cloud.yml \
         --outcome failed --out "$WORK/lost-$id.json" --commit "$sha" --log "$log" --note "$note" \
         --secret-env "$SECRET_ENV" --secret-file "$BACKEND/.env" >/dev/null 2>&1 \
       && python3 "$SITE/scripts/run_report.py" push --report "$WORK/lost-$id.json" --repo-dir "$SITE" >/dev/null 2>&1; then
      say "pushed a run report for run $id (reports/ctas-cloud/$id.json): $note"
    else
      say "WARN  could not push a report for run $id"
    fi
    [ -n "${GITHUB_STEP_SUMMARY:-}" ] && printf '\n**%s**: [run %s](%s)%s\n' "$note" "$id" "$url" "${why:+ — $why}" >>"$GITHUB_STEP_SUMMARY"
    # One line in the day's digest issue (scripts/release_issues.py); the watchdog alerts on a streak.
    GH_TOKEN="$DEPLOY_TOKEN" timeout 60 python3 "$SITE/scripts/release_issues.py" digest-add --kind lost-runner --key "ctas-cloud:$id" \
      --title "run $id of ctas-cloud.yml died with a lost runner (started ${created})" \
      --detail "$(grep 'last telemetry of the dead run' "$log" | tail -1 | cut -c1-300)" --run-url "$url" \
      --secret-env "$SECRET_ENV" --secret-file "$BACKEND/.env" >/dev/null 2>&1 || true
  done
}

on_exit() {
  local status=$?
  stop_collector
  rm -f -- "$FROZEN"
  stop_sampler
  resource_summary
  if [ "$status" -ne 0 ]; then
    [ -s "$DECISION" ] || printf '{"action": "block", "reason": "run-failed", "explanation": "the run failed (exit %s) before or outside the release checks", "main_commit": "%s", "blocking": []}\n' "$status" "$MAIN_COMMIT" >"$DECISION"
    sync_issues
    if [ "$FALLBACK_USED" -eq 1 ]; then push_report fallback
    elif [ -s "$CHECKS_MAIN" ] && grep -q '"outcome": "block"' "$CHECKS_MAIN"; then push_report blocked
    else push_report failed; fi
  elif [ "$FALLBACK_USED" -eq 1 ]; then
    push_report fallback
  fi
  return "$status"
}
trap on_exit EXIT

set_phase setup
start_sampler
add_swap
protect_agent
if [ "${NO_EXTRA_SWAP:-0}" = 1 ] && [ -z "${LIGHTCURVE_WORKERS_SET:-}" ]; then
  export CTAS_LIGHTCURVE_WORKERS=1
  say "light-curve workers: 1 (no extra swap on this runner)"
else
  say "light-curve workers: $CTAS_LIGHTCURVE_WORKERS"
fi
report_lost_runs

# ------------------------------------------------------------------ 1. restore
set_phase restore
say "restoring the database from the state repository"
RESTORE_LOG="$LOGS/restore.log"
if STATE_RECEIPT="$WORK/state-restored.json" GH_TOKEN="$STATE_TOKEN" \
  python3 "$SITE/scripts/ctas_cloud_state.py" restore "$DATA" >"$RESTORE_LOG" 2>&1; then
  cat "$RESTORE_LOG"
else
  cat "$RESTORE_LOG"
  # A restore refusal is an integrity check without fallback (older code must never accept
  # an archive the current safety rules refuse); it opens a release-blocked issue.
  CHECK_SECRET_ENV="$SECRET_ENV" CHECK_SECRET_FILE="$BACKEND/.env" python3 "$SITE/scripts/release_checks.py" record --scope ctas-publish --id ctas.restore \
    --exit-code 1 --output "$RESTORE_LOG" --results "$CHECKS_MAIN" >/dev/null 2>&1 || true
  decide >/dev/null
  fail "could not restore the state"
fi
# Public marker of this run (which state copy it restored and when that was saved,
# whether the state key still works and when it expires),
# published beside the release as ctas/data/cloud-run.json for the freshness watchdog.
MARKER="$WORK/cloud-run.json"
GH_TOKEN="$STATE_TOKEN" python3 "$SITE/scripts/cloud_run_marker.py" --workflow ctas-cloud.yml \
  --receipt "$WORK/state-restored.json" --output "$MARKER" --probe-token --site-commit "$MAIN_COMMIT" || { say "WARN  could not write the cloud-run marker"; MARKER=""; }

# ------------------------------------------------------------------ 2. collect
[ -f "$BACKEND/.env" ] || say "no .env for the collector; sources that need keys will be skipped"
# Retained public sky maps live beside the database and are kept between runs with it
# (scripts/ctas_cloud_state.py, skymaps.tar.gz) so that the hourly Track 3d refresh can
# re-rank a young event's candidates and galaxies in later runs.
export SKYMAP_ARTIFACT_DIR="$DATA/skymaps"
mkdir -p "$SKYMAP_ARTIFACT_DIR"
set_phase collect
say "starting the collector"
# Archived envelope payloads (scripts/ctas_envelope_archive.py) are fetched on demand by the
# collector's reader into a cache that is not saved; nothing in the cycle normally needs one.
( cd "$BACKEND" && CTAS_ENVELOPE_ARCHIVE_TOKEN="$STATE_TOKEN" CTAS_ENVELOPE_ARCHIVE_DIR="$WORK/envelope-archive-cache" \
    CTAS_ENVELOPE_ARCHIVE_REPO="${CTAS_STATE_REPO:-JackMcGuireAstro/ctas-state}" \
    exec supernova-ops api --host 127.0.0.1 --port "$PORT" ) >"$LOGS/collector.log" 2>&1 &
COLLECTOR_PID=$!
ready=0
for i in $(seq 1 600); do
  kill -0 "$COLLECTOR_PID" 2>/dev/null || { tail -40 "$LOGS/collector.log"; fail "the collector exited during start-up"; }
  if curl -fsS -m 3 "http://127.0.0.1:$PORT/api/v1/readiness" >/dev/null 2>&1; then ready=1; say "collector ready after ${i}s"; break; fi
  sleep 1
done
[ "$ready" -eq 1 ] || fail "the collector was not ready within 10 minutes"

END=$(( $(date +%s) + COLLECT_MINUTES * 60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  kill -0 "$COLLECTOR_PID" 2>/dev/null || { tail -40 "$LOGS/collector.log"; fail "the collector stopped unexpectedly"; }
  sleep 20
done
say "collected for $COLLECT_MINUTES minutes"

# ------------------------------------------------------------------ 3. publish
# The collector stops first: its memory plus the exporter's peak no longer fits the runner.
stop_collector
set_phase publish
PUBLISH_ARGS=()
[ "$DRY_RUN" = "1" ] && PUBLISH_ARGS=(--dry-run)
# CTAS_SNAPSHOT_METHOD is left unset: "auto" already takes a full backup on Linux (no
# clonefile), and setting it would leak into the publisher's own snapshot tests.
# DATA_BRANCH_COMPACT=0: this runner's data store lives for one run, so repacking the
# whole release after the push would only delay the save and the next cycle.
# CTAS_COLLECTOR_COMMIT names the collector code in ctas/data/provenance.json;
# CTAS_RELEASE_TOKEN lets the publisher replace the VOParquet assets of the rolling
# "ctas-data-latest" GitHub Release (contents: write) named in ctas/data/bulk-downloads.json.
COLLECTOR_COMMIT=$(git -C "$BACKEND" rev-parse HEAD 2>/dev/null || true)

# The snapshot the release is built from. The collector has stopped, so nothing writes the
# database any more: the publisher reads the database itself (read-only, after a checkpoint
# so the file alone holds every committed row, and an integrity check) instead of a second
# 6 GB copy beside it. If that cannot be prepared (the collector is somehow still there,
# or the check fails) the publisher freezes its own copy as before, and the copy a blocked
# run keeps for the fallback attempt is FROZEN. The fallback reads SNAPSHOT_DB either way.
SNAPSHOT_DB=""
JOURNAL_MODE=""
SNAPSHOT_ENV=()
if ! pgrep -f 'supernova-ops api' >/dev/null 2>&1; then
  JOURNAL_MODE=$(python3 - "$DATA/soc.db" 2>>"$LOGS/runner.log" <<'PYLIVE'
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], timeout=60)
try:
    mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("PRAGMA journal_mode=DELETE")
    verdict = connection.execute("PRAGMA quick_check").fetchall()
    if verdict != [("ok",)]:
        raise SystemExit(f"quick_check: {verdict[:3]}")
finally:
    connection.close()
print(mode)
PYLIVE
  ) && SNAPSHOT_DB="$DATA/soc.db" || JOURNAL_MODE=""
fi
if [ -n "$SNAPSHOT_DB" ]; then
  SNAPSHOT_ENV=(CTAS_FROZEN_DB="$SNAPSHOT_DB")
  say "the collector is stopped: the release is built from its database itself ($(stat -c %s "$SNAPSHOT_DB" | awk '{printf "%.2f", $1/1073741824}') GiB, read-only; no copy)"
  NEED_MB=$PUBLISH_MIN_FREE_MB
else
  say "WARN  could not hand over the stopped collector's database; the publisher freezes its own copy"
  NEED_MB=$(( PUBLISH_MIN_FREE_MB + $(stat -c %s "$DATA/soc.db" 2>/dev/null || echo 0) / 1048576 ))
fi

# Disk: refuse to start the release without room for it (the data store, the export, the
# bulk tables, and a snapshot copy when one is needed); the state is still saved.
GUARD_REASON=""
SITE_FREE=$(free_mb "$SITE/.git")
if [ "${SITE_FREE:-0}" -lt "$NEED_MB" ]; then
  GUARD_REASON="disk space: ${SITE_FREE:-0} MB free for the release, ${NEED_MB} MB needed"
  printf '%s (before the release)\n' "$GUARD_REASON" >"$GUARD_HIT"
  say "FAIL  $GUARD_REASON; the release is not started"
  PUBLISH_OUT=""
  PUBLISH_STATUS=1
else
  say "building the release${PUBLISH_ARGS:+ (dry run)} (${SITE_FREE} MB free, ${NEED_MB} MB needed; light-curve workers $CTAS_LIGHTCURVE_WORKERS)"
  # CTAS_CHECK_RESULTS collects the classified check results; CTAS_KEEP_FROZEN_DB keeps the
  # frozen database of a run a release check blocked, for the fallback attempt below.
  run_guarded publisher "$WORK/publish-out.txt" env "${SNAPSHOT_ENV[@]}" \
    CTAS_SITE="$SITE" CTAS_DB="$DATA/soc.db" CTAS_LOG_DIR="$LOGS/publish" CTAS_CLOUD_RUN_MARKER="$MARKER" \
    CTAS_COLLECTOR_COMMIT="$COLLECTOR_COMMIT" CTAS_RELEASE_TOKEN="$DEPLOY_TOKEN" \
    CTAS_CHECK_RESULTS="$CHECKS_MAIN" CTAS_KEEP_FROZEN_DB="$FROZEN" CHECK_SECRET_ENV="$SECRET_ENV" CHECK_SECRET_FILE="$BACKEND/.env" \
    DATA_BRANCH_DISPATCHER=0 DATA_BRANCH_COMPACT=0 bash "$SITE/scripts/publish_ctas.sh" "${PUBLISH_ARGS[@]}"
  PUBLISH_STATUS=$?
  PUBLISH_OUT=$(cat "$WORK/publish-out.txt" 2>/dev/null)
  rm -f -- "$WORK/publish-out.txt"
  [ -s "$GUARD_HIT" ] && GUARD_REASON=$(head -1 "$GUARD_HIT")
fi
printf '%s\n' "$PUBLISH_OUT" | tail -20
printf '%s\n' "$PUBLISH_OUT" | grep -E '^(FAIL|WARN)  ' >>"$LOGS/runner.log" || true
PUBLISHED=0
printf '%s\n' "$PUBLISH_OUT" | grep -q '^published ' && PUBLISHED=1
[ -n "$SNAPSHOT_DB" ] || SNAPSHOT_DB="$FROZEN"

# The one fallback attempt (scripts/code_fallback.sh): the code that produced the currently
# published release, on the same frozen database, under the same ceiling.
publish_with() {  # <site> <log dir> <results> <marker>
  run_guarded fallback "$WORK/fallback-out.txt" env \
    CTAS_SITE="$1" CTAS_DB="$DATA/soc.db" CTAS_FROZEN_DB="$SNAPSHOT_DB" CTAS_LOG_DIR="$2" CTAS_CHECK_RESULTS="$3" \
    CTAS_CLOUD_RUN_MARKER="$4" CTAS_COLLECTOR_COMMIT="$COLLECTOR_COMMIT" CTAS_RELEASE_TOKEN="$DEPLOY_TOKEN" \
    CHECK_SECRET_ENV="$SECRET_ENV" CHECK_SECRET_FILE="$BACKEND/.env" DATA_BRANCH_DISPATCHER=0 DATA_BRANCH_COMPACT=0 \
    bash "$1/scripts/publish_ctas.sh" "${PUBLISH_ARGS[@]}"
  local status=$?
  cat "$WORK/fallback-out.txt" 2>/dev/null
  rm -f -- "$WORK/fallback-out.txt"
  return "$status"
}
fallback_ready() { [ -s "$SNAPSHOT_DB" ]; }
if [ "$PUBLISH_STATUS" -eq 3 ]; then
  # The code that produced the currently published release (the publisher did not publish,
  # so its data store still holds that release).
  RELEASE_COMMIT=$(release_commit_of "$SITE" ctas)
  say "the published CTAS release was produced by site commit $(printf '%.10s' "${RELEASE_COMMIT:-unknown}"); this run used $(printf '%.10s' "$MAIN_COMMIT")"
fi
if [ -z "$GUARD_REASON" ]; then
  code_fallback_run ctas "$PUBLISH_STATUS"
else
  # The ceiling (or the disk check) stopped the release: no fallback attempt (it would need
  # the same memory), a block decision for the issues and the report, and the state is saved.
  [ "$PUBLISH_STATUS" -ne 0 ] || PUBLISH_STATUS=1
  case "$GUARD_REASON" in memory*) GUARD_CODE=memory-ceiling ;; "disk ceiling"*) GUARD_CODE=disk-ceiling ;; *) GUARD_CODE=disk-space ;; esac
  python3 - "$DECISION" "$GUARD_CODE" "$GUARD_REASON" "$MAIN_COMMIT" <<'PYGUARD' 2>>"$LOGS/runner.log" || true
import json, sys
path, code, reason, commit = sys.argv[1:5]
json.dump({"action": "block", "reason": code, "main_commit": commit, "blocking": [],
           "explanation": f"the runner guard stopped the release ({reason}); the database was still saved and the "
                          "published release is unchanged. See the phase lines and logs/resources.log."},
          open(path, "w"), indent=2, sort_keys=True)
PYGUARD
  echo "::error title=CTAS ${GUARD_CODE}::${GUARD_REASON}; the release was stopped cleanly and the state is saved"
fi
if [ "$FALLBACK_USED" -eq 1 ]; then
  PUBLISH_STATUS=0
  printf '%s\n' "$FALLBACK_OUT" | grep -q '^published ' && PUBLISHED=1
fi
[ -s "$GUARD_HIT" ] && [ -z "$GUARD_REASON" ] && say "WARN  the fallback attempt was stopped: $(head -1 "$GUARD_HIT")"
rm -f -- "$FROZEN"
rm -rf -- "$WORK/fallback-site"
# The collector's own journal mode again (it was switched to DELETE for the hand-over).
if [ -n "$JOURNAL_MODE" ] && [ "$JOURNAL_MODE" != "delete" ]; then
  python3 -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1], timeout=60); c.execute("PRAGMA journal_mode=" + sys.argv[2]); c.close()' \
    "$DATA/soc.db" "$JOURNAL_MODE" 2>>"$LOGS/runner.log" || say "WARN  could not set the database back to $JOURNAL_MODE mode"
fi

# ------------------------------------------------------------------ 4. save
stop_collector
set_phase retention
# Per-source counts for the last day go to the job summary, so a source that quietly
# fails on every request (e.g. a rejected key) shows up on the run page. Report only.
python3 "$SITE/scripts/ctas_source_health.py" --db "$DATA/soc.db" >"$LOGS/source-health.md" 2>&1 \
  && grep -m1 '^\*\*Needs attention' "$LOGS/source-health.md" | sed 's/^/source health: /' || true
# Keep derived bookkeeping bounded (scripts/ctas_db_retention.py; 2026-11-08 policy): of
# every analysis type except the published histories (light-curve inference, the GW
# localization/galaxy/counterpart/tiling revisions), keep the newest run and the newest
# complete run per candidate and type, everything younger than a day, and anything a
# reference file cites. Source data is never touched. The sizes go to the run log so the
# trend is visible run by run.
RAW_BEFORE=$(stat -c %s "$DATA/soc.db" 2>/dev/null || echo 0)
# Old raw payloads of the five high-volume photometry sources move to weekly assets of the
# state release (scripts/ctas_envelope_archive.py, 2026-11-16): at most
# CTAS_ENVELOPE_ARCHIVE_WEEKS weeks a run, each one uploaded, downloaded again and verified
# before any row changes; the collector reads them back on demand. Source data is never
# lost; a week that cannot be archived leaves the database as it was (advisory check
# ctas.envelope-archive). Not in dry runs.
# Only once the collector can read archived payloads (its envelope_archive.py).
if [ "$DRY_RUN" != "1" ] && [ "${CTAS_ENVELOPE_ARCHIVE:-1}" = "1" ] \
   && [ -f "$BACKEND/src/supernova_watch/envelope_archive.py" ]; then
  ARCHIVE_STATUS=0
  # Time limit: an interrupted week rolls back (SQLite journal); nothing half-archived stays.
  GH_TOKEN="$STATE_TOKEN" timeout --foreground "${CTAS_ENVELOPE_ARCHIVE_TIMEOUT:-1500}" python3 "$SITE/scripts/ctas_envelope_archive.py" run --db "$DATA/soc.db" \
    --work "$WORK/envelope-archive" --report "$WORK/envelope-archive.json" >"$LOGS/envelope-archive.log" 2>&1 || ARCHIVE_STATUS=$?
  grep -E '^(archived|archive moved|nothing to archive|WARN|FAIL)' "$LOGS/envelope-archive.log" | sed 's/^/envelope archive: /' || true
  [ "$ARCHIVE_STATUS" -eq 0 ] || say "WARN  the envelope archive step failed (exit $ARCHIVE_STATUS); see envelope-archive.log"
  rm -rf -- "$WORK/envelope-archive"
  CHECK_SECRET_ENV="$SECRET_ENV" CHECK_SECRET_FILE="$BACKEND/.env" python3 "$SITE/scripts/release_checks.py" record --scope ctas-publish \
    --id ctas.envelope-archive --exit-code "$ARCHIVE_STATUS" --output "$LOGS/envelope-archive.log" \
    --results "$CHECKS_MAIN" || true
fi
python3 "$SITE/scripts/ctas_db_retention.py" daily --db "$DATA/soc.db" >"$LOGS/retention.log" 2>&1 \
  && say "trimmed superseded derived records" || say "database retention skipped this cycle (see retention.log)"
grep -E '^.*(prune|deleted|database [0-9])' "$LOGS/retention.log" | sed 's/^/retention: /' || true
say "database size $(awk -v b="$RAW_BEFORE" 'BEGIN{printf "%.2f", b/1073741824}') GiB -> $(stat -c %s "$DATA/soc.db" 2>/dev/null | awk '{printf "%.2f", $1/1073741824}') GiB raw after retention"
SAVE_STATUS=0
if [ "$DRY_RUN" = "1" ]; then
  say "dry run: the database is not saved and nothing was pushed"
else
  set_phase save
  say "saving the database to the state repository"
  STATE_SIZE_REPORT="$WORK/state-size.json" GH_TOKEN="$STATE_TOKEN" python3 "$SITE/scripts/ctas_cloud_state.py" save "$DATA" \
    >"$LOGS/save.log" 2>&1 || SAVE_STATUS=$?
  cat "$LOGS/save.log"
  # The size as the advisory check ctas.state-size ("state growing"): a check-warning issue
  # above 1.4 GiB compressed that closes itself once the size is back under it. The copy is
  # saved in parts below GitHub's 2 GiB file limit (since 2026-11-16); it is refused only above
  # 20 GiB raw, against the runner's disk (the previous state is then kept).
  if [ -s "$WORK/state-size.json" ]; then
    SIZE_VERDICT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "$WORK/state-size.json" 2>/dev/null || echo ok)
    CHECK_SECRET_ENV="$SECRET_ENV" CHECK_SECRET_FILE="$BACKEND/.env" python3 "$SITE/scripts/release_checks.py" record --scope ctas-publish \
      --id ctas.state-size --exit-code "$([ "$SIZE_VERDICT" = ok ] && echo 0 || echo 1)" --output "$LOGS/save.log" \
      --results "$CHECKS_MAIN" || true
  fi
fi
sync_issues
if [ "$SAVE_STATUS" -eq 4 ]; then
  say "FAIL  STATE TOO LARGE: the database was not saved (over the raw-size line for the runner's disk, CTAS_STATE_REFUSE_RAW_GIB); the previous state is kept and CTAS keeps publishing from it. Trim it: see save.log, scripts/ctas_db_retention.py and scripts/ctas_envelope_archive.py"
  echo "::error title=CTAS state too large::the collector database was not saved (over the raw-size line for the runner's disk); the previous state is kept. See the run log."
  [ -n "${GITHUB_STEP_SUMMARY:-}" ] && printf '\n## STATE TOO LARGE\n\nThe collector database was not saved: it is over the raw-size line (CTAS_STATE_REFUSE_RAW_GIB, 20 GiB by default; a cycle needs about three times the raw size on the runner'"'"'s disk). The previous state is kept and CTAS keeps publishing from it, but each cycle'"'"'s new data is lost until the database is trimmed.\n' >>"$GITHUB_STEP_SUMMARY"
  exit 1
fi
[ "$SAVE_STATUS" -eq 0 ] || { say "FAIL  could not save the database; the previous state is kept"; exit 1; }

# ------------------------------------------------------------------ 5. deploy
set_phase deploy
if [ "$PUBLISHED" -eq 1 ] && [ "$DRY_RUN" != "1" ]; then
  # A push made with the workflow token does not start other workflows by itself.
  GH_TOKEN="$DEPLOY_TOKEN" gh workflow run worldsindex-release.yml --repo "${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}" --ref main \
    && say "deployment requested" || say "could not request the deployment; the next run or a main push will deploy it"
fi
# GitHub's hourly schedule is unreliable for this repository (runs arrive hours apart),
# so each successful cycle starts the next one; the hourly schedule only restarts the
# chain if it ever breaks. A cycle that could not save its database does not chain.
if [ "${CHAIN:-1}" = "1" ] && [ "$DRY_RUN" != "1" ]; then
  GH_TOKEN="$DEPLOY_TOKEN" gh workflow run ctas-cloud.yml --repo "${GITHUB_REPOSITORY:-JackMcGuireAstro/JackMcGuireAstro.github.io}" --ref main \
    && say "next cycle requested" || say "could not request the next cycle; the hourly schedule will start it"
fi
[ "$PUBLISH_STATUS" -eq 0 ] || { say "the publisher reported a problem (exit $PUBLISH_STATUS); see the publish log artifact"; exit "$PUBLISH_STATUS"; }
say "done"
