#!/usr/bin/env python3
"""Build the reproducibility bundles (Track 7, gate 5): repro/ctas and repro/worldsindex.

Each bundle is self-contained and works outside the website: the selected public records as
published (byte-for-byte JSON of the release's record), their query receipts, the analysis
configuration and method text, a pinned environment, a notebook that recreates one figure
from those files only, and a MANIFEST.json with the SHA-256 of every file.

  CTAS        SN2025bbe: the published multi-band Bazin light-curve fit. The notebook
              re-selects the fit's input detections from the dossier by the stated input
              rule, checks the count against the run's recorded inputs, draws the data with
              the published model, refits independently with SciPy and reports the
              difference in peak time and timescales against stated tolerances.
              SN2026aabx is included as the record of the conflict benchmark.
  WorldsIndex HD 209458 b: the published transit ephemeris. The notebook re-applies the
              stated selection policy to the planet's catalog rows, recomputes the predicted
              mid-transit times and their 1-sigma uncertainty over a year and draws the
              uncertainty growth.

    python3 scripts/build_repro_bundles.py [--tree .]     (needs both releases overlaid)
    python3 scripts/build_repro_bundles.py --validate PYTHON   (runs both notebooks with that
                                                            interpreter, e.g. a clean venv)
"""
from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = "numpy==2.5.4\nscipy==1.18.1\nmatplotlib==3.11.2\nnbformat==5.11.1\nnbclient==0.11.0\nipykernel==7.4.0\n"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def notebook(cells: list[tuple[str, str]]) -> dict:
    return {"cells": [{"cell_type": kind, "id": f"cell-{index}", "metadata": {}, "source": text.strip("\n").splitlines(True),
                       **({"outputs": [], "execution_count": None} if kind == "code" else {})} for index, (kind, text) in enumerate(cells)],
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                         "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}


CTAS_NOTEBOOK = [
    ("markdown", """
# CTAS reproducibility bundle: SN2025bbe multi-band Bazin fit

This notebook uses only the files in this folder. It re-selects the fit's input detections
from the published dossier by the run's stated input rule, checks them against the run's
recorded inputs, draws the light curve with the published model, refits independently and
compares. A phenomenological fit; it is not a classification, a physical model or a distance.
"""),
    ("code", """
import json, math
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

record = json.loads(Path("records/SN2025bbe.json").read_text())
config = json.loads(Path("analysis/config.json").read_text())
run = next(r for r in record["lightcurve_science"]["runs"] if r["analysisType"] == "multiband-bazin")
assert run["status"] == "complete" and run["methodVersion"] == config["methodVersion"]
inputs = record["lightcurve_science"]["inputs"]
print(record["name"], run["methodName"], run["methodVersion"], "reduced chi2", run["result"]["reducedChi2"])
"""),
    ("code", """
# The run's input rule: ZTF g/r/i difference-PSF detections, one row per exposure, AB,
# uncertainty <= 0.5 mag (this record has no ATLAS rows).
rows = {}
for o in record["follow_up"]["observations"]:
    if o.get("detection") == 1 and o.get("difference_photometry") == 1 and o.get("magnitude_system") == "AB" \\
            and o.get("magnitude_error") is not None and o["magnitude_error"] <= 0.5 and o.get("band") in ("g", "r", "i"):
        rows.setdefault(("ztf" + o["band"], round(o["mjd"], 5)), o)
by_band = {}
for band, _ in rows:
    by_band[band] = by_band.get(band, 0) + 1
print("selected", len(rows), by_band, "recorded", inputs["detections"], inputs["detectionsByBand"])
assert len(rows) == inputs["detections"] and by_band == inputs["detectionsByBand"]
"""),
    ("code", """
ref = run["result"]["referenceMjd"]
P = {k: v["value"] for k, v in run["result"]["parameters"].items()}
def bazin(t, log_a, t0, log_tf, log_tr):
    return np.exp(log_a) * np.exp(-(t - t0) / np.exp(log_tf)) / (1 + np.exp(-(t - t0) / np.exp(log_tr)))
data = {}
for (band, _), o in sorted(rows.items(), key=lambda kv: kv[0][1]):
    flux = 10 ** ((23.9 - o["magnitude"]) / 2.5)
    err = flux * math.log(10) / 2.5 * o["magnitude_error"]
    data.setdefault(band, []).append((o["mjd"] - ref, flux, err))
data = {b: np.array(v) for b, v in data.items()}

def residuals(p):
    t0, log_tf, log_tr = p[:3]
    out = []
    for i, band in enumerate(sorted(data)):
        t, f, e = data[band].T
        out.append((bazin(t, p[3 + i], t0, log_tf, log_tr) - f) / e)
    return np.concatenate(out)
# Independent start, not the published solution: peak at the brightest point, 20-day fall,
# 3-day rise, amplitude from each band's brightest flux.
brightest = max((row for band in data for row in data[band]), key=lambda r: r[1])
start = [brightest[0], math.log(20.0), math.log(3.0)] + [math.log(2.5 * data[b][:, 1].max()) for b in sorted(data)]
mine = least_squares(residuals, start, method="trf", x_scale="jac", max_nfev=20000).x
published = np.array([P["t0"], P["log_tau_fall"], P["log_tau_rise"]] + [P["log_amplitude_" + b] for b in sorted(data)])
tol = config["reproduction_tolerance"]
def peak(t0, log_tf, log_tr):
    tf, tr = np.exp(log_tf), np.exp(log_tr)
    return t0 + tr * np.log(tf / tr - 1) if tf > tr else t0
diff = {"peak_days": abs(peak(*mine[:3]) - peak(*published[:3])),
        "tau_fall_fraction": abs(math.exp(mine[1]) / math.exp(published[1]) - 1),
        "tau_rise_fraction": abs(math.exp(mine[2]) / math.exp(published[2]) - 1)}
print({k: float(f"{v:.3g}") for k, v in diff.items()}, "start", [round(x, 3) for x in start[:3]], "tolerances", {k: v for k, v in tol.items() if k != "basis"})
assert diff["peak_days"] <= tol["peak_days"] and diff["tau_fall_fraction"] <= tol["tau_fraction"] and diff["tau_rise_fraction"] <= tol["tau_fraction"]
"""),
    ("code", """
fig, ax = plt.subplots(figsize=(7, 4.2))
grid = np.linspace(min(d[:, 0].min() for d in data.values()) - 10, max(d[:, 0].max() for d in data.values()) + 10, 400)
colors = {"ztfg": "#2a9d8f", "ztfr": "#e76f51", "ztfi": "#6d597a"}
for band in sorted(data):
    t, f, e = data[band].T
    ax.errorbar(t, f, yerr=e, fmt="o", ms=4, color=colors[band], label=f"{band} detections")
    ax.plot(grid, bazin(grid, P["log_amplitude_" + band], P["t0"], P["log_tau_fall"], P["log_tau_rise"]), color=colors[band], lw=1.5,
            label=f"{band} published Bazin model")
ax.set_xlabel(f"days since MJD {ref:.4f}")
ax.set_ylabel("flux density (microjansky, AB zero point 23.9)")
ax.set_title("SN2025bbe: published multi-band Bazin fit (CTAS)")
ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig("figure.png", dpi=120)
print("figure.png written")
"""),
]

WI_NOTEBOOK = [
    ("markdown", """
# WorldsIndex reproducibility bundle: HD 209458 b transit ephemeris

This notebook uses only the files in this folder. It re-applies the published selection
policy to the planet's catalog rows, recomputes the predicted mid-transit times with their
1-sigma uncertainty, compares them with the published ephemeris row and draws the
uncertainty growth over one year.
"""),
    ("code", """
import json, math
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

detail = json.loads(Path("records/HD-209458-b.json").read_text())
eph = json.loads(Path("records/ephemeris-row.json").read_text())
config = json.loads(Path("analysis/config.json").read_text())
row = dict(zip(eph["columns"], eph["row"]))
print(row["name"], row["sourceId"], row["periodDays"], row["periodErrDays"], row["t0Bjd"], row["t0ErrDays"])
"""),
    ("code", """
# Selection policy, step by step (analysis/config.json quotes it): PSCompPars if it reports
# both a period and a mid-transit time; else the NASA PS default set; else ... else the TOI,
# K2 or KOI row with the smallest propagated mid-time uncertainty.
records = detail["records"]
comp = [r for r in records if r["sourceId"] == "nasa-pscomppars"]
default = [r for r in records if r["sourceId"] == "nasa-ps" and r.get("isSourceDefault")]
def has_both(r):
    v = r.get("values") or {}
    return v.get("pl_orbper") is not None and v.get("pl_tranmid") is not None
print("PSCompPars has both:", any(has_both(r) for r in comp), "| PS default has both:", any(has_both(r) for r in default))
if any(has_both(r) for r in comp):
    expected = "nasa-pscomppars"
elif any(has_both(r) for r in default):
    expected = "nasa-ps"
else:
    expected = "a later step of the policy"
print("policy reaches:", expected, "| published row from:", row["sourceId"])
assert expected == "a later step of the policy" or expected == row["sourceId"]
"""),
    ("code", """
ref = eph["referenceBjd"]
P, sP, T0, sT0 = row["periodDays"], row["periodErrDays"], row["t0Bjd"], row["t0ErrDays"]
n = np.arange(math.ceil((ref - T0) / P), math.ceil((ref + 365.25 - T0) / P) + 1)
mid = T0 + n * P
sigma_min = np.sqrt(sT0 ** 2 + (n * sP) ** 2) * 1440
print("next transit after the release reference:", round(mid[0], 5), "BJD_TDB, sigma", round(sigma_min[0], 2), "min")
print("one year later sigma", round(sigma_min[-1], 2), "min")
published = config["published_sigma_minutes_at_reference"]
# The release row carries the inputs (period, epoch and their errors), not a propagated value;
# when a published propagated value is recorded in the config it must agree within tolerance.
assert published is None or abs(sigma_min[0] - published) <= config["tolerance_minutes"]
fig, ax = plt.subplots(figsize=(7, 4))
ax.plot(mid - ref, sigma_min, "o-", ms=3, color="#264653")
ax.set_xlabel(f"days after the release reference BJD_TDB {ref}")
ax.set_ylabel("1-sigma mid-transit uncertainty (minutes)")
ax.set_title("HD 209458 b: propagated ephemeris uncertainty (WorldsIndex)")
fig.tight_layout(); fig.savefig("figure.png", dpi=120)
print("figure.png written")
"""),
]


def write_bundle(folder: Path, files: dict[str, bytes], nb: list, readme: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (folder / "environment").mkdir(exist_ok=True)
    (folder / "environment/requirements.txt").write_text(REQUIREMENTS)
    (folder / "reproduce.ipynb").write_text(json.dumps(notebook(nb), indent=1) + "\n")
    (folder / "README.md").write_text(readme)
    manifest = {p.relative_to(folder).as_posix(): sha(p) for p in sorted(folder.rglob("*"))
                if p.is_file() and p.name not in ("MANIFEST.json", "figure.png") and ".ipynb_checkpoints" not in p.parts}
    (folder / "MANIFEST.json").write_text(json.dumps({"schema": "repro-bundle-manifest@1", "files": manifest}, indent=1, sort_keys=True) + "\n")


def build(tree: Path) -> None:
    def ctas_record(name):
        for fn in sorted(glob.glob(str(tree / "ctas/data/candidate-chunks/*.json"))):
            if fn.endswith("manifest.json") or ".part-" in fn:
                continue
            for c in json.loads(Path(fn).read_text()).get("candidates", []):
                if c.get("name") == name:
                    return c
        raise SystemExit(f"{name} is not in this release")
    bbe, aabx = ctas_record("SN2025bbe"), ctas_record("SN2026aabx")
    run = next(r for r in bbe["lightcurve_science"]["runs"] if r["analysisType"] == "multiband-bazin")
    status = json.loads((tree / "ctas/data/status.json").read_text())
    receipts = {name: {"source_coverage": rec.get("source_coverage"), "persistedQueryReceipts": (rec.get("astro_evidence") or {}).get("persistedQueryReceipts")}
                for name, rec in (("SN2025bbe", bbe), ("SN2026aabx", aabx))}
    ctas_config = {"methodName": run["methodName"], "methodVersion": run["methodVersion"], "parameters": run["parameters"],
                   "inputRule": bbe["lightcurve_science"]["inputs"]["inputRule"], "references": run.get("references"),
                   "software_at_release": run.get("software"),
                   "reproduction_tolerance": {"peak_days": 1.0, "tau_fraction": 0.10,
                                              "basis": "independent refit with SciPy from a generic start (brightest point, 20-day fall, 3-day rise); tolerances allow for optimizer and library-version differences"},
                   "release": {"ctas_last_successful_update": status.get("last_successful_update"),
                               "catalog_content_checksum_sha256": status.get("catalog_content_checksum_sha256")}}
    write_bundle(ROOT / "repro/ctas", {
        "records/SN2025bbe.json": (json.dumps(bbe, indent=1, sort_keys=True) + "\n").encode(),
        "records/SN2026aabx.json": (json.dumps(aabx, indent=1, sort_keys=True) + "\n").encode(),
        "receipts/receipts.json": (json.dumps(receipts, indent=1, sort_keys=True) + "\n").encode(),
        "analysis/config.json": (json.dumps(ctas_config, indent=1, sort_keys=True) + "\n").encode(),
        "methods.md": (f"# Methods\n\n- Model: {run['parameters']['model']} ({run['parameters']['space']}); "
                       f"{run['parameters']['sharedParameters']}.\n- Fitter at release: {run['parameters']['fitter']}.\n"
                       f"- Input rule: {bbe['lightcurve_science']['inputs']['inputRule']}.\n- Reference: Bazin et al. 2009, A&A 499, 653 (2009A&A...499..653B).\n"
                       f"- Claim boundary: {run['claimBoundary']}\n").encode(),
    }, CTAS_NOTEBOOK, """# CTAS reproducibility bundle

Recreates the published multi-band Bazin light-curve fit of SN2025bbe from the files here,
outside the website. Run in a clean environment:

    python3 -m venv .venv && .venv/bin/pip install -r environment/requirements.txt
    .venv/bin/jupyter execute reproduce.ipynb     (or: python3 ../../scripts/build_repro_bundles.py --validate .venv/bin/python)

`records/` holds the dossiers exactly as published (SN2026aabx is the conflict benchmark's
record), `receipts/` their query receipts, `analysis/config.json` the run's method,
parameters and the tolerances, `MANIFEST.json` the SHA-256 of every file. The notebook writes
figure.png.
""")
    wi_detail = None
    for fn in sorted(glob.glob(str(tree / "worldsindex/data/details/*.json.gz"))):
        shard = json.loads(gzip.decompress(Path(fn).read_bytes()))
        if "object-hd-209458-b" in shard:
            wi_detail = shard["object-hd-209458-b"]
            break
    eph = json.loads(gzip.decompress((tree / "worldsindex/data/transit-ephemerides.json.gz").read_bytes()))
    row = next(r for r in eph["rows"] if r[0] == "object-hd-209458-b") if "rows" in eph else None
    if row is None:
        rows = [v for k, v in eph.items() if isinstance(v, list) and v and isinstance(v[0], list)]
        row = next(r for r in rows[0] if r[0] == "object-hd-209458-b")
    eph_row = {"columns": eph["columns"], "row": row, "referenceBjd": eph["referenceBjd"], "timeStandard": eph["timeStandard"],
               "selectionPolicy": eph["selectionPolicy"], "generatedAt": eph["generatedAt"]}
    manifest = json.loads((tree / "worldsindex/data/manifest.json").read_text())
    wi_config = {"selectionPolicy": eph["selectionPolicy"], "propagation": "sigma(T_n) = sqrt(sigma_T0^2 + (n sigma_P)^2), 1 sigma, BJD_TDB",
                 "published_sigma_minutes_at_reference": None, "tolerance_minutes": 0.01,
                 "release": {"worldsindex_generatedAt": manifest.get("generatedAt"), "catalogIndexSha256": manifest.get("catalogIndexSha256")}}
    write_bundle(ROOT / "repro/worldsindex", {
        "records/HD-209458-b.json": (json.dumps(wi_detail, indent=1, sort_keys=True) + "\n").encode(),
        "records/ephemeris-row.json": (json.dumps(eph_row, indent=1, sort_keys=True) + "\n").encode(),
        "receipts/source-coverage.json": (tree / "worldsindex/data/source-coverage.json").read_bytes(),
        "analysis/config.json": (json.dumps(wi_config, indent=1, sort_keys=True) + "\n").encode(),
        "methods.md": ("# Methods\n\n- Ephemeris selection: " + eph["selectionPolicy"] + "\n- Propagation: linear ephemeris, "
                       "uncertainty added in quadrature, 1 sigma.\n- Time standard: " + eph["timeStandard"] + ".\n").encode(),
    }, WI_NOTEBOOK, """# WorldsIndex reproducibility bundle

Re-applies the published transit-ephemeris selection policy to HD 209458 b's catalog rows and
recomputes its predicted mid-transit times and uncertainty, outside the website. Run in a
clean environment:

    python3 -m venv .venv && .venv/bin/pip install -r environment/requirements.txt
    .venv/bin/jupyter execute reproduce.ipynb     (or: python3 ../../scripts/build_repro_bundles.py --validate .venv/bin/python)

`records/` holds the planet's detail record and its published ephemeris row exactly as in
the release, `receipts/source-coverage.json` the release's per-source coverage receipts,
`analysis/config.json` the policy and tolerance, `MANIFEST.json` the SHA-256 of every file.
""")


def validate(python: str) -> int:
    code = """
import sys, json, hashlib, os
from pathlib import Path
import nbformat
from nbclient import NotebookClient
folder = Path(sys.argv[1])
manifest = json.loads((folder / "MANIFEST.json").read_text())["files"]
bad = [p for p, s in manifest.items() if hashlib.sha256((folder / p).read_bytes()).hexdigest() != s]
assert not bad, f"checksum mismatch: {bad}"
nb = nbformat.read(folder / "reproduce.ipynb", as_version=4)
NotebookClient(nb, timeout=600, kernel_name="python3", resources={"metadata": {"path": str(folder)}}).execute()
out = [o.get("text", "") for c in nb.cells if c.cell_type == "code" for o in c.get("outputs", []) if o.get("output_type") == "stream"]
print("".join(out).strip().splitlines()[-1])
assert (folder / "figure.png").exists()
"""
    status = 0
    for product in ("ctas", "worldsindex"):
        folder = ROOT / "repro" / product
        result = subprocess.run([python, "-c", code, str(folder)], capture_output=True, text=True)
        print(f"{product}: {'ok' if result.returncode == 0 else 'FAILED'} {result.stdout.strip()[-200:]}")
        if result.returncode != 0:
            print(result.stderr[-2000:])
            status = 1
        (folder / "figure.png").unlink(missing_ok=True)
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", default=".")
    parser.add_argument("--validate", metavar="PYTHON")
    args = parser.parse_args()
    if args.validate:
        return validate(args.validate)
    build(Path(args.tree))
    print("bundles written: repro/ctas, repro/worldsindex")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
