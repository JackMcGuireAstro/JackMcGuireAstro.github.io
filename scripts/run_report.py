#!/usr/bin/env python3
"""Sanitized failure reports of the cloud runs and the deploy, on the public branch run-reports.

When a CTAS or WorldsIndex publish run, or a "Validate and deploy site" run, fails or uses
fallback code, it writes one small JSON report: workflow, run id, commit, the failing
phase and check ids, and at most 200 lines of evidence (the publisher's own FAIL, WARN and
phase lines, the validators' error lines and the failing check's output tail). It is pushed
to the branch ``run-reports``:

    reports/<workflow>/latest.json      the newest report of that workflow
    reports/<workflow>/<run_id>.json    one file per run; the newest 50 per workflow are kept

so a failure can be read with nothing but raw.githubusercontent.com or ``git fetch``,
without the Actions UI or a login. The branch is one parentless commit replaced with
``--force-with-lease`` (compare-and-swap: concurrent writers retry, none is lost), so it
never grows; ``--extra`` adds other small files (the deploy's check history).

The repository is public, so everything written passes ``sanitize``: the values of every
secret-bearing environment variable the workflows set (``SECRET_ENV_NAMES``, kept complete
by scripts/test_run_report.py, which reads the workflow files), their base64 forms and the
``x-access-token:`` form git sends, the values of the collector's and builder's settings
files, GitHub token shapes (ghp_, gho_, ghs_, ghu_, ghr_, github_pat_), Authorization
headers, Bearer tokens, AWS access keys and secret keys, Cloudflare API keys and tokens,
``*_TOKEN=``-style assignments and credentials inside URLs become ``***``. After that,
``leaks`` scans the result with looser patterns; if anything is still found the report is
refused (exit 2) and nothing is written or pushed.

    run_report.py sanitize [--secret-env NAMES] [--secret-file .env]  < text  > text
    run_report.py build --workflow ctas-cloud.yml --outcome failed|fallback|blocked --out report.json
                        [--log publish.log ...] [--results checks.jsonl ...] [--decision decision.json]
                        [--commit SHA] [--secret-env NAMES] [--secret-file .env] [--note TEXT]
    run_report.py push  --report report.json [--repo-dir .] [--branch run-reports] [--keep 50]
                        [--extra history/deploy.json=deploy-history.json]
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

REPORT_SCHEMA = "run-report@2"
MASK = "***"
# Every environment variable a workflow of this repository sets from a secret or from the
# workflow token (scripts/test_run_report.py fails when a workflow adds one not listed here).
SECRET_ENV_NAMES = (
    "STATE_TOKEN", "DEPLOY_TOKEN", "GH_TOKEN", "PUSH_TOKEN", "BACKEND_ENV", "BUILDER_ENV",
    "AAVSO_AID_API_TOKEN_OVERRIDE", "WATCHDOG_NOTIFY_WEBHOOK", "CTAS_RELEASE_TOKEN", "WORLDSINDEX_RELEASE_TOKEN",
)
TOKEN_PATTERNS = (
    re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{8,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{12,}\b"),          # AWS access key ids
    re.compile(r"\bv1\.0-[0-9a-f]{24}-[0-9a-f]{100,}\b"),                                   # Cloudflare origin CA keys
)
ASSIGNMENTS = (
    # AWS secret keys, Cloudflare API keys and tokens, and any *_TOKEN / *_SECRET / *PASSWORD /
    # *_API_KEY / *_ACCESS_KEY assignment (environment or settings-file style).
    re.compile(r"(?i)\b(aws_secret_access_key|aws_session_token|x-auth-key|x-auth-email|cf[_-]?api[_-]?(?:token|key)|cloudflare[_-]?api[_-]?(?:token|key))(\s*[:=]\s*)(?!\*\*\*)\S+"),
    re.compile(r"\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY|PRIVATE_KEY))(\s*=\s*)(?!\*\*\*)\S+"),
)
AUTH_HEADER = re.compile(r"(?i)\b((?:proxy-)?authorization)\s*[:=]\s*(?:(?:basic|bearer|token)\s+)?(?!\*\*\*)\S+")
BEARER = re.compile(r"(?i)\b(bearer)\s+(?!\*\*\*)[A-Za-z0-9._~+/=-]{8,}|\b(token)\s+(?!\*\*\*)[A-Za-z0-9._~+/=-]{12,}")
URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)(?!\*\*\*@)[^/\s:@'\"]+(?::[^/\s@'\"]*)?@")
ACCESS_TOKEN = re.compile(r"(?i)\bx-access-token:(?!\*\*\*)[^\s@'\"]+")
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)
# Looser shapes for the final scan: anything here after sanitizing refuses the report.
LEAK_PATTERNS = (
    re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{4,}"),
    re.compile(r"\bgithub_pat_\w{4,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{8,}"),
    re.compile(r"(?i)\bbearer\s+(?!\*\*\*)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bauthorization\s*[:=]\s*(?!\*\*\*)(?:basic\s+|bearer\s+|token\s+)?(?!\*\*\*)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\baws_secret_access_key\s*[:=]\s*(?!\*\*\*)\S"),
    re.compile(r"(?i)x-access-token:(?!\*\*\*)\S"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
FAIL_LINE = re.compile(r"(?:^|\s)(?:FAIL|ERROR)(?:\s{1,2}|: )\S")
WARN_LINE = re.compile(r"(?:^|\s)WARN\s{1,2}\S")
PHASE_LINE = re.compile(r"phase\s+(\S+) took (\d+)s")
VALIDATOR_LINE = re.compile(r"^\s{2,}(?:\S.*::|.*\.schema\.json:|.*published but not declared|Traceback|\w+Error\b|AssertionError)")
MAX_LINES = 200
TAIL = 60
KEEP = 50
README = """# run-reports

Sanitized reports of failed or fallback runs of the CTAS and WorldsIndex cloud publishers
and of the "Validate and deploy site" workflow, written by scripts/run_report.py on main.
`reports/<workflow>/latest.json` is the newest report of each workflow and
`reports/<workflow>/<run_id>.json` the newest 50 per workflow. Secrets, tokens,
Authorization headers and URL credentials are removed before a report is written, and a
report in which a credential shape survives is refused. `history/deploy.json` holds the
deploy's watch-mode pass counts. See RESILIENCE.md on main.
"""


class LeakError(RuntimeError):
    """A credential shape survived sanitizing: the report must not be written."""


# ----------------------------------------------------------------------------- sanitizer
def dotenv_values(path: str | Path | None) -> list[str]:
    """Values of KEY=VALUE lines of a settings file (4+ characters), never printed."""
    if not path or not Path(path).is_file():
        return []
    values = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if len(value) >= 4:
                values.append(value)
    return values


def env_secret_values(names: str | Iterable[str] | None, environ: dict | None = None) -> list[str]:
    """Values of the named environment variables (all of SECRET_ENV_NAMES when ``names`` is
    None); a multi-line value (a settings-file secret) contributes each of its values too."""
    environ = os.environ if environ is None else environ
    if names is None:
        names = SECRET_ENV_NAMES
    elif isinstance(names, str):
        names = re.split(r"[,\s]+", names)
    values = []
    for name in names or ():
        value = environ.get(name) if name else None
        if not value:
            continue
        values.append(value)
        for line in value.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                values.append(line.split("=", 1)[1].strip().strip('"').strip("'"))
            elif line.strip() and line.strip() != value:
                values.append(line.strip())
    return [value for value in values if len(value) >= 4]


def secret_forms(values: Iterable[str]) -> list[str]:
    """Each secret value with the encoded forms it can appear in: base64 of the value and of
    ``x-access-token:<value>`` (git's Basic header) and the URL-safe base64 variants."""
    forms = set()
    for value in values:
        if not value or len(value) < 4:
            continue
        forms.add(value)
        for raw in (value, f"x-access-token:{value}"):
            encoded = base64.b64encode(raw.encode()).decode()
            forms.update({encoded, encoded.rstrip("="), base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")})
    return sorted((form for form in forms if len(form) >= 4), key=len, reverse=True)


def sanitize(text: str, secret_values: Iterable[str] = ()) -> str:
    """Remove secrets from text that is about to become public."""
    if not text:
        return text or ""
    for value in secret_forms(secret_values):
        text = text.replace(value, MASK)
    text = PRIVATE_KEY.sub(MASK, text)
    for pattern in TOKEN_PATTERNS:
        text = pattern.sub(MASK, text)
    for pattern in ASSIGNMENTS:
        text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    text = AUTH_HEADER.sub(lambda m: f"{m.group(1)}: {MASK}", text)
    text = BEARER.sub(lambda m: f"{m.group(1) or m.group(2)} {MASK}", text)
    text = ACCESS_TOKEN.sub(f"x-access-token:{MASK}", text)
    text = URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}{MASK}@", text)
    return text


def leaks(text: str, secret_values: Iterable[str] = ()) -> list[str]:
    """What still looks like a credential after sanitizing (shapes, never the values)."""
    found = []
    for value in secret_forms(secret_values):
        if value in text:
            found.append("a secret value")
            break
    for pattern in LEAK_PATTERNS:
        if pattern.search(text):
            found.append(f"pattern {pattern.pattern[:40]}")
    return found


def sanitize_tree(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return sanitize(value, secrets)
    if isinstance(value, list):
        return [sanitize_tree(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_tree(item, secrets) for key, item in value.items()}
    return value


def checked_json(document: dict, secrets: Iterable[str]) -> str:
    """The document as JSON text, refused (LeakError) when a credential shape survives."""
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    found = leaks(text, secrets)
    if found:
        raise LeakError("refusing to write a report that still contains " + ", ".join(sorted(set(found))))
    return text


# ----------------------------------------------------------------------------- report
def read_jsonl(path: str | Path) -> list[dict]:
    rows = []
    if path and Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def evidence_lines(logs: Iterable[str], limit: int = MAX_LINES) -> tuple[list[dict], list[str], str | None]:
    """(phases, lines, failing phase): the publisher's FAIL/WARN/phase lines and the
    validators' error lines from the logs, at most ``limit`` lines in all."""
    phases, lines, failing_phase = [], [], None
    for path in logs:
        if not path or not Path(path).is_file():
            continue
        last_phase = None
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            match = PHASE_LINE.search(line)
            if match:
                last_phase = match.group(1)
                phases.append({"log": Path(path).name, "phase": match.group(1), "seconds": int(match.group(2))})
            keep = match or FAIL_LINE.search(line) or WARN_LINE.search(line) or VALIDATOR_LINE.search(line)
            if FAIL_LINE.search(line) and failing_phase is None:
                failing_phase = f"after {last_phase}" if last_phase else "before the first phase"
            if keep and len(lines) < limit:
                lines.append(f"{Path(path).name}: {line.strip()[:400]}")
    return phases, lines, failing_phase


def build_report(*, workflow: str, outcome: str, logs: Iterable[str] = (), results: Iterable[dict] = (),
                 decision: dict | None = None, commit: str | None = None, environ: dict | None = None,
                 secrets: Iterable[str] = (), note: str | None = None, now: dt.datetime | None = None) -> dict:
    """The sanitized report document. Pure apart from reading the log files named."""
    environ = os.environ if environ is None else environ
    now = now or dt.datetime.now(dt.timezone.utc)
    secrets = list(secrets)
    rows = [row for row in results if not row.get("family")]
    problems = [row for row in rows if row.get("outcome") in ("block", "warn")]
    blocking = [row for row in rows if row.get("outcome") == "block"]
    first = blocking[0] if blocking else (problems[0] if problems else None)
    tail = (first.get("output_tail") or [])[-TAIL:] if first else []
    phases, lines, failing_phase = evidence_lines(logs, MAX_LINES - len(tail))
    run_id = environ.get("GITHUB_RUN_ID")
    server = environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = environ.get("GITHUB_REPOSITORY")
    report = {
        "schema": REPORT_SCHEMA,
        "written_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "workflow": workflow,
        "run_id": run_id,
        "run_attempt": environ.get("GITHUB_RUN_ATTEMPT"),
        "run_url": f"{server}/{repository}/actions/runs/{run_id}" if run_id and repository else None,
        "event": environ.get("GITHUB_EVENT_NAME"),
        "commit": commit or environ.get("GITHUB_SHA"),
        "outcome": outcome,
        "note": note,
        "failing_phase": failing_phase,
        "failing_checks": [row.get("id") for row in blocking],
        "warning_checks": [row.get("id") for row in problems if row.get("outcome") == "warn"],
        "phases": phases,
        "lines": lines,
        "failing_check": {
            "id": first.get("id"), "scope": first.get("scope"), "outcome": first.get("outcome"),
            "class": first.get("class"), "mode": first.get("mode"), "reason": first.get("reason"),
            "parts": [part.get("part") for part in first.get("parts", [])[:20]], "output_tail": tail,
        } if first else None,
        "checks": [{"id": row.get("id"), "scope": row.get("scope"), "outcome": row.get("outcome"),
                    "class": row.get("class"), "mode": row.get("mode"),
                    "parts": [part.get("part") for part in row.get("parts", [])[:10]]} for row in problems][:80],
        "check_counts": {key: sum(1 for row in rows if row.get("outcome") == key) for key in ("pass", "warn", "block", "skip")},
        "decision": decision,
        "sanitized": True,
    }
    return sanitize_tree(report, secrets)


def workflow_slug(workflow: str | None) -> str:
    slug = re.sub(r"\.ya?ml$", "", str(workflow or "workflow"))
    return re.sub(r"[^A-Za-z0-9._-]+", "-", slug) or "workflow"


def report_paths(report: dict) -> tuple[str, str]:
    workflow = workflow_slug(report.get("workflow"))
    run_id = re.sub(r"[^0-9A-Za-z_-]+", "-", str(report.get("run_id") or "local"))
    return f"reports/{workflow}/{run_id}.json", f"reports/{workflow}/latest.json"


def prune(paths: list[str], keep: int = KEEP) -> list[str]:
    """Report paths to remove so that the newest ``keep`` per workflow remain (by run id)."""
    by_workflow: dict[str, list[tuple[int, str]]] = {}
    for path in paths:
        match = re.fullmatch(r"reports/([^/]+)/(\d+)\.json", path)
        if match:
            by_workflow.setdefault(match.group(1), []).append((int(match.group(2)), path))
    remove = []
    for rows in by_workflow.values():
        rows.sort()
        remove.extend(path for _, path in rows[:-keep] if len(rows) > keep)
    return sorted(remove)


# ----------------------------------------------------------------------------- push
def git(repo: str, *args: str, env: dict | None = None, input_text: str | None = None, check: bool = True) -> str:
    completed = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                               env={**os.environ, **(env or {})}, input=input_text)
    if check and completed.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {sanitize(completed.stderr.strip(), env_secret_values(None))[:300]}")
    return completed.stdout.strip()


def push_files(repo: str, branch: str, files: dict[str, str], message: str, keep: int = KEEP,
               remote: str = "origin", attempts: int = 5) -> str:
    """Replace ``branch`` on ``remote`` with one parentless commit: the current tree plus
    ``files`` (branch path -> text), pruned to ``keep`` reports per workflow. Returns the
    new commit."""
    last_error = None
    for _ in range(attempts):
        listing = git(repo, "ls-remote", remote, f"refs/heads/{branch}", check=False)
        tip = listing.split()[0] if listing else ""
        tracking = f"refs/run-reports-tip/{branch}"
        if tip:
            git(repo, "fetch", "--quiet", "--no-tags", "--depth=1", remote, f"+refs/heads/{branch}:{tracking}")
        with tempfile.TemporaryDirectory(prefix="run-reports-") as scratch:
            env = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
            git(repo, "read-tree", tracking if tip else "--empty", env=env)
            entries = dict(files)
            entries["README.md"] = README
            for path, text in entries.items():
                blob = git(repo, "hash-object", "-w", "--stdin", input_text=text)
                git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            for path in prune(git(repo, "ls-files", env=env).splitlines(), keep):
                git(repo, "update-index", "--force-remove", "--", path, env=env)
            tree = git(repo, "write-tree", env=env)
        identity = {
            "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "Run reports"),
            "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "41898282+github-actions[bot]@users.noreply.github.com"),
        }
        identity["GIT_COMMITTER_NAME"] = identity["GIT_AUTHOR_NAME"]
        identity["GIT_COMMITTER_EMAIL"] = identity["GIT_AUTHOR_EMAIL"]
        commit = git(repo, "commit-tree", tree, env=identity, input_text=message + "\n")
        pushed = subprocess.run(["git", "-C", repo, "push", "--quiet", f"--force-with-lease=refs/heads/{branch}:{tip}",
                                 remote, f"{commit}:refs/heads/{branch}"], capture_output=True, text=True)
        if pushed.returncode == 0:
            return commit
        last_error = sanitize(pushed.stderr.strip(), env_secret_values(None))[:300]
    raise RuntimeError(f"could not update {branch} after {attempts} attempts: {last_error}")


# ----------------------------------------------------------------------------- CLI
def collect_secrets(secret_env: str | None, secret_files: Iterable[str]) -> list[str]:
    names = list(SECRET_ENV_NAMES) + (re.split(r"[,\s]+", secret_env) if secret_env else [])
    return env_secret_values(names) + [value for path in secret_files for value in dotenv_values(path)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sanitize")
    p.add_argument("--secret-env", default="", help="more variable names (all of SECRET_ENV_NAMES are always used)")
    p.add_argument("--secret-file", action="append", default=[])
    p = sub.add_parser("build")
    p.add_argument("--workflow", required=True)
    p.add_argument("--outcome", required=True, choices=["failed", "fallback", "blocked"])
    p.add_argument("--out", required=True)
    p.add_argument("--log", action="append", default=[])
    p.add_argument("--results", action="append", default=[])
    p.add_argument("--decision")
    p.add_argument("--commit")
    p.add_argument("--note")
    p.add_argument("--secret-env", default="")
    p.add_argument("--secret-file", action="append", default=[])
    p = sub.add_parser("push")
    p.add_argument("--report", help="a report built by `build` (optional when only --extra files are pushed)")
    p.add_argument("--repo-dir", default=".")
    p.add_argument("--branch", default="run-reports")
    p.add_argument("--remote", default="origin")
    p.add_argument("--keep", type=int, default=KEEP)
    p.add_argument("--extra", action="append", default=[], help="branch/path=local-file to add as well")
    p.add_argument("--secret-env", default="")
    p.add_argument("--secret-file", action="append", default=[])
    args = parser.parse_args(argv)
    secrets = collect_secrets(args.secret_env, args.secret_file)

    if args.command == "sanitize":
        sys.stdout.write(sanitize(sys.stdin.read(), secrets))
        return 0
    if args.command == "build":
        rows = [row for path in args.results for row in read_jsonl(path)]
        decision = None
        if args.decision and Path(args.decision).is_file():
            try:
                decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
            except ValueError:
                decision = None
        report = build_report(workflow=args.workflow, outcome=args.outcome, logs=args.log, results=rows,
                              decision=decision, commit=args.commit, secrets=secrets, note=args.note)
        try:
            text = checked_json(report, secrets)
        except LeakError as error:
            Path(args.out).unlink(missing_ok=True)
            print(f"run report: {error}; nothing written", file=sys.stderr)
            return 2
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"run report: {args.outcome}; {len(report['lines'])} evidence line(s); failing check(s) "
              f"{', '.join(report['failing_checks']) or 'none'}")
        return 0
    files, label = {}, "files"
    if args.report:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        if report.get("sanitized") is not True:
            print("refusing to push a report that was not built by run_report.py build", file=sys.stderr)
            return 2
        try:
            text = checked_json(report, secrets)
        except LeakError as error:
            print(f"run report: {error}; nothing pushed", file=sys.stderr)
            return 2
        one, latest = report_paths(report)
        files = {one: text, latest: text}
        label = one
        message = f"run report: {report.get('workflow')} run {report.get('run_id')} ({report.get('outcome')})"
    else:
        message = "run reports: update " + ", ".join(item.partition("=")[0] for item in args.extra)
    for item in args.extra:
        branch_path, _, local = item.partition("=")
        if branch_path and local and Path(local).is_file():
            extra = Path(local).read_text(encoding="utf-8")
            if leaks(extra, secrets):
                print(f"run report: refusing to push {branch_path}: a credential shape is in it", file=sys.stderr)
                return 2
            files[branch_path] = extra
    if not files:
        print("nothing to push")
        return 0
    commit = push_files(args.repo_dir, args.branch, files, message, keep=args.keep, remote=args.remote)
    print(f"pushed {label} to {args.branch} ({commit[:10]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
