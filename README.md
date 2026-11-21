# run-reports

Sanitized reports of failed or fallback runs of the CTAS and WorldsIndex cloud publishers
and of the "Validate and deploy site" workflow, written by scripts/run_report.py on main.
`reports/<workflow>/latest.json` is the newest report of each workflow and
`reports/<workflow>/<run_id>.json` the newest 50 per workflow. Secrets, tokens,
Authorization headers and URL credentials are removed before a report is written, and a
report in which a credential shape survives is refused. `history/deploy.json` holds the
deploy's watch-mode pass counts. See RESILIENCE.md on main.
