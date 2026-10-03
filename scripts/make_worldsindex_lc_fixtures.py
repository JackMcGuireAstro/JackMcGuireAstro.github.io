#!/usr/bin/env python3
"""Write the small synthetic light-curve FITS files used by test_worldsindex_live_mast.mjs.

The files imitate the layout of real MAST products (a header-only primary HDU, a LIGHTCURVE
binary table, an APERTURE image) closely enough to exercise worldsindex/assets/live-mast.js:

  tess-spoc-synthetic_lc.fits  TESS SPOC 2-minute style: BJDREFI 2457000, PDCSAP + SAP flux,
                               NaN gaps, QUALITY bits that the default mask drops (32, 128) and
                               keeps (4096), an unsigned 16-bit column (written with TZERO) and
                               a box transit (P = 0.3 d, 1.2 h, 10 ppt) on a slow trend.
  kepler-synthetic_llc.fits    Kepler 30-minute style: BJDREFI 2454833, QUARTER, PDCSAP all NaN
                               (so the reader must fall back to SAP_FLUX).

expected.json records what astropy reads back, so the JavaScript reader is checked against an
independent FITS implementation. Re-run after changing this script:
    python3 scripts/make_worldsindex_lc_fixtures.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from astropy.io import fits

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "worldsindex-lightcurves"
TESS_DEFAULT_MASK = 175
KEPLER_DEFAULT_MASK = 1130799


def primary(**cards) -> fits.PrimaryHDU:
    hdu = fits.PrimaryHDU()
    for key, value in cards.items():
        hdu.header[key] = value
    return hdu


def tess_file() -> dict:
    rng = np.random.RandomState(20261004)
    n = 540  # 0.75 d of 2-minute cadences
    btjd = 1683.35 + np.arange(n) * (2.0 / 1440.0)
    period, t0_btjd, duration_d, depth = 0.3, 1683.5, 1.2 / 24.0, 0.010
    phase = ((btjd - t0_btjd) / period + 0.5) % 1.0 - 0.5
    in_transit = np.abs(phase * period) < duration_d / 2
    trend = 1.0 + 0.004 * (btjd - btjd[0])
    model = trend * np.where(in_transit, 1.0 - depth, 1.0)
    pdcsap = (12000.0 * (model + rng.normal(0.0, 0.0006, n))).astype(np.float32)
    sap = (pdcsap * np.float32(1.02)).astype(np.float32)
    err = np.full(n, 7.5, dtype=np.float32)
    quality = np.zeros(n, dtype=np.int32)
    quality[[10, 11, 200]] = 32       # momentum dump: dropped by the default mask
    quality[300] = 128                # manual exclude: dropped by the default mask
    quality[[50, 51, 52, 400]] = 4096  # stray light: kept by default, dropped by strict
    pdcsap[[100, 101, 102]] = np.nan  # pipeline gap
    btjd_with_gap = btjd.copy()
    btjd_with_gap[480] = np.nan       # missing time stamp
    columns = [
        fits.Column(name="TIME", format="D", unit="BJD - 2457000, days", array=btjd_with_gap),
        fits.Column(name="TIMECORR", format="E", unit="d", array=np.full(n, 0.0012, dtype=np.float32)),
        fits.Column(name="CADENCENO", format="J", array=np.arange(100000, 100000 + n, dtype=np.int32)),
        fits.Column(name="SAP_FLUX", format="E", unit="e-/s", array=sap),
        fits.Column(name="SAP_FLUX_ERR", format="E", unit="e-/s", array=err),
        fits.Column(name="PDCSAP_FLUX", format="E", unit="e-/s", array=pdcsap),
        fits.Column(name="PDCSAP_FLUX_ERR", format="E", unit="e-/s", array=err),
        fits.Column(name="QUALITY", format="J", array=quality),
        fits.Column(name="MOM_CENTR1", format="D", unit="pixel", array=np.full(n, 1024.5)),
        fits.Column(name="X_UNSIGNED", format="I", bzero=32768, array=(np.arange(n) * 100).astype(np.uint16)),
    ]
    table = fits.BinTableHDU.from_columns(columns, name="LIGHTCURVE")
    for key, value in (("TIMEREF", "SOLARSYSTEM"), ("TIMESYS", "TDB"), ("BJDREFI", 2457000), ("BJDREFF", 0.0),
                       ("TIMEUNIT", "d"), ("TIMEDEL", 2.0 / 1440.0), ("OBJECT", "TIC 25155310"), ("TICID", 25155310)):
        table.header[key] = value
    aperture = fits.ImageHDU(np.ones((5, 5), dtype=np.int32), name="APERTURE")
    hdul = fits.HDUList([primary(TELESCOP="TESS", OBJECT="TIC 25155310", TICID=25155310, SECTOR=14, CAMERA=1, CCD=2,
                                 ORIGIN="synthetic fixture for WorldsIndex tests"), table, aperture])
    path = OUT / "tess-spoc-synthetic_lc.fits"
    hdul.writeto(path, overwrite=True, output_verify="exception")
    with fits.open(path) as check:
        data = check[1].data
        finite = np.isfinite(data["TIME"]) & np.isfinite(data["PDCSAP_FLUX"])
        default_keep = finite & ((data["QUALITY"].astype(np.int64) & TESS_DEFAULT_MASK) == 0)
        strict_keep = finite & (data["QUALITY"] == 0)
        return {
            "file": path.name, "rows": int(len(data)), "bjdref": 2457000.0,
            "time0": float(data["TIME"][0]), "pdcsap0": float(data["PDCSAP_FLUX"][0]), "sap0": float(data["SAP_FLUX"][0]),
            "quality200": int(data["QUALITY"][200]), "unsigned539": int(data["X_UNSIGNED"][539]),
            "defaultKept": int(default_keep.sum()), "strictKept": int(strict_keep.sum()), "missing": int((~finite).sum()),
            "transit": {"periodDays": period, "t0Bjd": 2457000.0 + t0_btjd, "durationHours": 1.2, "depthPpt": depth * 1000},
            "sector": 14, "ticid": 25155310,
        }


def kepler_file() -> dict:
    n = 96  # two days of 30-minute cadences
    bkjd = 351.25 + np.arange(n) * (29.4244 / 1440.0)
    sap = (40000.0 + 5.0 * np.sin(np.arange(n) / 7.0)).astype(np.float32)
    quality = np.zeros(n, dtype=np.int32)
    quality[[3, 4]] = 1048576   # no fine point (K2/Kepler thruster bit): dropped by default
    quality[60] = 16            # zero crossing: kept by default
    columns = [
        fits.Column(name="TIME", format="D", unit="BJD - 2454833", array=bkjd),
        fits.Column(name="SAP_FLUX", format="E", unit="e-/s", array=sap),
        fits.Column(name="SAP_FLUX_ERR", format="E", unit="e-/s", array=np.full(n, 6.0, dtype=np.float32)),
        fits.Column(name="PDCSAP_FLUX", format="E", unit="e-/s", array=np.full(n, np.nan, dtype=np.float32)),
        fits.Column(name="PDCSAP_FLUX_ERR", format="E", unit="e-/s", array=np.full(n, np.nan, dtype=np.float32)),
        fits.Column(name="QUALITY", format="J", array=quality),
    ]
    table = fits.BinTableHDU.from_columns(columns, name="LIGHTCURVE")
    for key, value in (("TIMESYS", "TDB"), ("BJDREFI", 2454833), ("BJDREFF", 0.0), ("TIMEDEL", 0.0204335)):
        table.header[key] = value
    hdul = fits.HDUList([primary(TELESCOP="Kepler", MISSION="Kepler", OBJECT="KIC 11446443", KEPLERID=11446443, QUARTER=4,
                                 ORIGIN="synthetic fixture for WorldsIndex tests"), table])
    path = OUT / "kepler-synthetic_llc.fits"
    hdul.writeto(path, overwrite=True, output_verify="exception")
    with fits.open(path) as check:
        data = check[1].data
        keep = (data["QUALITY"].astype(np.int64) & KEPLER_DEFAULT_MASK) == 0
        return {"file": path.name, "rows": int(len(data)), "bjdref": 2454833.0, "time0": float(data["TIME"][0]),
                "sap0": float(data["SAP_FLUX"][0]), "defaultKept": int(keep.sum()), "quarter": 4, "keplerid": 11446443}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    expected = {"tess": tess_file(), "kepler": kepler_file()}
    (OUT / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")
    for name in ("tess-spoc-synthetic_lc.fits", "kepler-synthetic_llc.fits"):
        print(f"{name}: {(OUT / name).stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
