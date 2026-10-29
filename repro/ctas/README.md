# CTAS reproducibility bundle

Recreates the published multi-band Bazin light-curve fit of SN2025bbe from the files here,
outside the website. Run in a clean environment:

    python3 -m venv .venv && .venv/bin/pip install -r environment/requirements.txt
    .venv/bin/jupyter execute reproduce.ipynb     (or: python3 ../../scripts/build_repro_bundles.py --validate .venv/bin/python)

`records/` holds the dossiers exactly as published (SN2026aabx is the conflict benchmark's
record), `receipts/` their query receipts, `analysis/config.json` the run's method,
parameters and the tolerances, `MANIFEST.json` the SHA-256 of every file. The notebook writes
figure.png.
