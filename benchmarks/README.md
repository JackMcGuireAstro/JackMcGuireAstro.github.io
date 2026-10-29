# Benchmarks

Results kept in the repository with the code that produced them (Track 7). They are not
announced anywhere. Each file names its inputs, the release or commit it was measured on,
and how to rerun it.

| Folder | What | Made by | Last measured |
|---|---|---|---|
| `identity/` | WorldsIndex identity gold set (73 cases): false merges and false splits against thresholds | `npm run identity:gold` in worldsindex-state (copied here) | 2026-10-28 |
| `sources/` | Four denominators and receipt outcomes per source, both products | `python3 scripts/build_benchmarks.py` on a tree with both releases overlaid | 2026-10-29 |
| `science/` | The framework's four fixture dossiers from real records: facts and what a reviewer should see | `python3 scripts/build_benchmarks.py` | 2026-10-29 |
| `performance/` | Largest Contentful Paint and first-load JavaScript per main page, throttled mobile profile; per-page budgets | `python3 scripts/test_release_pages_browser.py` (BUDGET_REPORT=…) | 2026-10-29 |
| `recovery/` | Backup-and-restore drill of the saved state archives | `python3 scripts/state_restore_drill.py` | 2026-10-29 (CTAS; WorldsIndex pending) |

Checks: `browser.release_pages` (budgets and the page-audit fixes) and `release.evidence`
(these files are well formed, the science fixtures' facts still hold on the release being
deployed, the reproducibility manifests match their files) run at every deploy. Both are
ADVISORY: a failure opens one GitHub issue and never blocks a deploy.

Promotion rule: a check moves to INTEGRITY (blocking once proven) when it has passed on real
deploys for 14 consecutive days with no change to its budgets, and the owner agrees; the
budgets ratchet down when a page improves (edit `performance/budgets.json`). The framework's
targets (LCP ≤ 2.5 s, ≤ 200 kB JavaScript) are recorded beside the ratchet; pages over a
target are listed in `../KNOWN_LIMITATIONS.md` until they meet it.
