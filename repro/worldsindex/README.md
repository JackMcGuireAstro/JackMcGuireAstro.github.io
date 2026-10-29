# WorldsIndex reproducibility bundle

Re-applies the published transit-ephemeris selection policy to HD 209458 b's catalog rows and
recomputes its predicted mid-transit times and uncertainty, outside the website. Run in a
clean environment:

    python3 -m venv .venv && .venv/bin/pip install -r environment/requirements.txt
    .venv/bin/jupyter execute reproduce.ipynb     (or: python3 ../../scripts/build_repro_bundles.py --validate .venv/bin/python)

`records/` holds the planet's detail record and its published ephemeris row exactly as in
the release, `receipts/source-coverage.json` the release's per-source coverage receipts,
`analysis/config.json` the policy and tolerance, `MANIFEST.json` the SHA-256 of every file.
