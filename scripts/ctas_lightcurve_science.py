"""Light-curve science for CTAS dossiers (Framework v2 Track 3c, part 1).

Every public event whose retained photometry is good enough gets a ``lightcurve_science``
block (``ctas.lightcurve-science@1``): a list of analysis runs, each naming its method and
version, its exact inputs (with a checksum), its parameters, its result (with a
checksum), warnings and a claim boundary. A run that cannot be computed says why; it never
carries a default value.

Runs, in order:

* ``multiband-bazin`` and ``multiband-villar``: one joint fit across the fitted bands with
  shared timing parameters and a per-band amplitude (Bazin et al. 2009; Villar et al. 2019),
  in flux space, weighted least squares.
* ``lightcurve-features``: from the fit with the lower AICc, per band: time of peak, peak
  apparent magnitude, half-maximum rise time, half-maximum duration, decline rate over 15
  days after peak; uncertainties from 200 seeded draws of the fit covariance.
* ``absolute-magnitude``: where the event has a redshift: peak absolute magnitude with the
  Planck 2018 distance modulus, Galactic extinction from the SFD (1998) map recalibrated by
  Schlafly & Finkbeiner (2011) with a Fitzpatrick (1999) R_V = 3.1 law at each band's
  effective wavelength, and the first-order bandwidth term of the k-correction
  (+2.5 log10(1 + z)); no spectral k-correction is applied (stated, not hidden).
* ``jump-detector``: the latest detection in each band against the detections between
  70 and 10 days before it (Lasair-style baseline window).
* ``periodogram``: Lomb-Scargle per band with the Baluev false-alarm probability.
* ``salt3``: an sncosmo SALT3 fit for events classified as type Ia, or scored as Ia by a
  broker (rule stated in the run).
* ``screens``: fast-transient (kilonova-like timescale) and nuclear-transient flags. They
  are screens, not claims.

The per-epoch colour and blackbody temperature are not recomputed here: the collector's
multiband colour screen is already published under ``analysis_screens`` (Track 3a), and
the features run points to it. A weekly bolometric (superbol) job is not part of this part.

Photometry: ZTF g/r/i difference-image PSF detections (ALeRCE, Fink, Lasair) and ATLAS o/c
forced-PSF detections, AB magnitudes. The same ZTF exposure relayed by several brokers is
kept once (the row with the smallest uncertainty). Other bands and pipelines are counted
as excluded inputs, never mixed into the fit.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

CANDIDATE_KEY = "lightcurve_science"
SCHEMA = "ctas.lightcurve-science@1"
RUN_TYPES = (
    "multiband-bazin", "multiband-villar", "lightcurve-features", "absolute-magnitude",
    "jump-detector", "periodogram", "salt3", "screens",
)
ZP_UJY = 23.9  # AB magnitude of 1 microjansky
# Fitted bands: canonical key -> (label, effective wavelength in Angstrom, sncosmo bandpass)
BANDS = {
    "ztfg": ("ZTF g", 4746.5, "ztfg"),
    "ztfr": ("ZTF r", 6366.4, "ztfr"),
    "ztfi": ("ZTF i", 7829.0, "ztfi"),
    "atlasc": ("ATLAS c", 5330.0, "atlasc"),
    "atlaso": ("ATLAS o", 6780.0, "atlaso"),
}
ZTF_DIFFERENCE_PROVIDERS = {"ztf-alerce", "rubin-fink", "rubin-lasair"}
PROVIDER_PREFERENCE = {"ztf-alerce": 0, "rubin-fink": 1, "rubin-lasair": 2, "atlas": 0}
MIN_BAND_DETECTIONS = 3
MIN_TOTAL_DETECTIONS = 8
MIN_SPAN_DAYS = 5.0
MAX_MAG_ERROR = 0.5
MC_DRAWS = 200
JUMP_SIGMA = 5.0
PERIOD_MIN_DETECTIONS = 20
PERIOD_MIN_SPAN_DAYS = 30.0
PERIOD_FAP_THRESHOLD = 1e-3
FAST_RISE_DAYS = 3.0
FAST_DECLINE_MAG_PER_DAY = 0.3
NUCLEAR_OFFSET_ARCSEC = 0.5
SFD_RECALIBRATION = 0.86
R_V = 3.1
VERSION = "1.0.0"
CLAIM_BOUNDARY = (
    "A phenomenological fit to public difference and forced photometry; it describes the "
    "light-curve shape and is not a classification, a physical model or a distance."
)


# ----------------------------------------------------------------------------- helpers
def _sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _round(value: float | None, digits: int = 5) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    if value == 0:
        return 0.0
    return float(f"{value:.{digits}g}")


def _software() -> dict[str, str]:
    import numpy
    import scipy
    versions = {"numpy": numpy.__version__, "scipy": scipy.__version__}
    try:
        import astropy
        versions["astropy"] = astropy.__version__
    except ImportError:  # pragma: no cover - astropy is a collector requirement
        pass
    return versions


def band_key(row: dict[str, Any]) -> str | None:
    """The fitted-band key of a photometry row, or None when the row is not fitted."""

    provider = str(row.get("provider") or "").lower()
    original = str(row.get("original_band") or row.get("band") or "").lower()
    system = str(row.get("magnitude_system") or "").upper()
    if system and system != "AB":
        return None
    if provider in ZTF_DIFFERENCE_PROVIDERS and original in {"ztf-g", "ztf-r", "ztf-i"}:
        return "ztf" + original[-1]
    if provider == "atlas" and original in {"o", "c"}:
        return "atlas" + original
    return None


def usable_detections(observations: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Fitted detections (deduplicated) and the counts of excluded rows by reason."""

    excluded: dict[str, int] = {}

    def skip(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    best: dict[tuple[str, int], dict[str, Any]] = {}
    for row in observations:
        if not row.get("detection"):
            continue
        if row.get("superseded"):
            skip("superseded")
            continue
        flags = row.get("quality_flags") or []
        if isinstance(flags, str):
            try:
                decoded = json.loads(flags)
            except ValueError:
                decoded = [flags]
            flags = decoded if isinstance(decoded, list) else [flags]
        if any(flag in {"scientific-fit-excluded", "negative-difference-flux"} for flag in flags):
            skip("flagged-not-for-fits")
            continue
        key = band_key(row)
        if key is None:
            skip("band-or-pipeline-not-fitted")
            continue
        try:
            mjd = float(row.get("mjd"))
            magnitude = float(row.get("magnitude"))
            error = float(row.get("magnitude_error"))
        except (TypeError, ValueError):
            skip("missing-time-magnitude-or-error")
            continue
        if not (math.isfinite(mjd) and math.isfinite(magnitude) and math.isfinite(error)) or not 0 < error <= MAX_MAG_ERROR:
            skip("uncertainty-missing-or-above-0.5-mag")
            continue
        provider = str(row.get("provider") or "").lower()
        slot = (key, round(mjd * 1e4))  # one ZTF exposure relayed by several brokers
        candidate = {"band": key, "mjd": mjd, "mag": magnitude, "err": error, "provider": provider,
                     "id": str(row.get("assertion_id") or "")}
        current = best.get(slot)
        if current is None or (error, PROVIDER_PREFERENCE.get(provider, 9), candidate["id"]) < (
                current["err"], PROVIDER_PREFERENCE.get(current["provider"], 9), current["id"]):
            if current is not None:
                skip("same-exposure-duplicate")
            best[slot] = candidate
        else:
            skip("same-exposure-duplicate")
    rows = sorted(best.values(), key=lambda item: (item["mjd"], item["band"], item["id"]))
    return rows, dict(sorted(excluded.items()))


def input_summary(rows: list[dict[str, Any]], excluded: dict[str, int]) -> dict[str, Any]:
    per_band: dict[str, int] = {}
    for row in rows:
        per_band[row["band"]] = per_band.get(row["band"], 0) + 1
    digest = _sha256([[row["band"], round(row["mjd"], 6), round(row["mag"], 4), round(row["err"], 4)] for row in rows])
    summary = {
        "detections": len(rows),
        "detectionsByBand": dict(sorted(per_band.items())),
        "excludedRows": excluded,
        "inputChecksumSha256": digest,
        "inputRule": ("ZTF g/r/i difference-PSF detections (ALeRCE, Fink, Lasair; one row per "
                      "exposure) and ATLAS o/c forced-PSF detections, AB, uncertainty <= 0.5 mag"),
    }
    if rows:
        summary["firstMjd"] = _round(rows[0]["mjd"], 10)
        summary["lastMjd"] = _round(rows[-1]["mjd"], 10)
    return summary


def _run(kind: str, method: str, *, inputs: dict[str, Any], parameters: dict[str, Any],
         result: dict[str, Any] | None = None, reason: str | None = None,
         warnings: list[str] | None = None, claim: str = CLAIM_BOUNDARY,
         software: dict[str, str] | None = None, references: list[str] | None = None) -> dict[str, Any]:
    run: dict[str, Any] = {
        "analysisType": kind,
        "methodName": method,
        "methodVersion": VERSION,
        "status": "complete" if result is not None else "not-computed",
        "inputs": inputs,
        "inputChecksumSha256": _sha256(inputs),
        "parameters": parameters,
        "warnings": warnings or [],
        "claimBoundary": claim,
    }
    if references:
        run["references"] = references
    if software:
        run["software"] = software
    if result is not None:
        run["result"] = result
        run["resultChecksumSha256"] = _sha256(result)
    else:
        run["notComputedReason"] = reason or "not computed"
    return run


# ----------------------------------------------------------------------------- models
def _bazin(t, t0, tau_rise, tau_fall):
    import numpy as np
    x = np.clip(-(t - t0) / tau_rise, -60, 60)
    y = np.clip(-(t - t0) / tau_fall, -60, 60)
    return np.exp(y) / (1.0 + np.exp(x))


def _villar(t, t0, gamma, tau_rise, tau_fall, slope):
    import numpy as np
    t1 = t0 + gamma
    rise = 1.0 / (1.0 + np.exp(np.clip(-(t - t0) / tau_rise, -60, 60)))
    before = (1.0 + slope * (t - t0)) * rise
    after = (1.0 + slope * gamma) * np.exp(np.clip(-(t - t1) / tau_fall, -60, 60)) * rise
    return np.where(t < t1, before, after)


class _Fit:
    def __init__(self, model: str, names: list[str], params, cov, chi2: float, dof: int, bands: list[str], tref: float):
        self.model, self.names, self.params, self.cov = model, names, params, cov
        self.chi2, self.dof, self.bands, self.tref = chi2, dof, bands, tref
        n = dof + len(params)
        k = len(params)
        self.aicc = chi2 + 2 * k + (2 * k * (k + 1) / (n - k - 1) if n - k - 1 > 0 else float("inf"))

    def shape(self, p, t):
        # Covariance draws can wander far outside the fit bounds; clamp the log scales.
        def scale(value):
            return math.exp(min(max(float(value), -20.0), 20.0))
        if self.model == "bazin":
            t0, ltr, ltf = p[0], p[1], p[2]
            return _bazin(t, t0, scale(ltr), scale(ltf))
        t0, lg, ltr, ltf, slope = p[0], p[1], p[2], p[3], p[4]
        return _villar(t, t0, scale(lg), scale(ltr), scale(ltf), slope)

    def n_shape(self) -> int:
        return 3 if self.model == "bazin" else 5


def fit_model(model: str, rows: list[dict[str, Any]]):
    """Joint fit with shared timing and per-band amplitudes; returns (_Fit, warnings) or (None, reason)."""

    import numpy as np
    from scipy.optimize import least_squares

    bands = sorted({row["band"] for row in rows})
    index = {band: i for i, band in enumerate(bands)}
    t = np.array([row["mjd"] for row in rows])
    flux = 10 ** (-0.4 * (np.array([row["mag"] for row in rows]) - ZP_UJY))
    sigma = flux * math.log(10) / 2.5 * np.array([row["err"] for row in rows])
    band_index = np.array([index[row["band"]] for row in rows])
    tref = float(t[np.argmax(flux)])
    tt = t - tref
    n_shape = 3 if model == "bazin" else 5
    peak_by_band = np.array([flux[band_index == i].max() for i in range(len(bands))])

    def residuals(p):
        shape_params = p[:n_shape]
        amplitudes = np.exp(p[n_shape:])[band_index]
        if model == "bazin":
            shape = _bazin(tt, shape_params[0], math.exp(shape_params[1]), math.exp(shape_params[2]))
        else:
            shape = _villar(tt, shape_params[0], math.exp(shape_params[1]), math.exp(shape_params[2]),
                            math.exp(shape_params[3]), shape_params[4])
        return (amplitudes * shape - flux) / sigma

    span = float(tt.max() - tt.min())
    if model == "bazin":
        lower = [tt.min() - 50, math.log(0.1), math.log(0.5)] + [-30] * len(bands)
        upper = [tt.max() + 30, math.log(100), math.log(500)] + [30] * len(bands)
        starts = [[t0, math.log(3), math.log(30)] for t0 in (-10.0, -5.0, -2.0, 0.0)]
    else:
        lower = [tt.min() - 50, math.log(0.5), math.log(0.1), math.log(0.5), -0.1] + [-30] * len(bands)
        upper = [tt.max() + 30, math.log(300), math.log(100), math.log(500), 0.0] + [30] * len(bands)
        starts = [[t0, math.log(10), math.log(3), math.log(30), -0.01] for t0 in (-10.0, -5.0, 0.0)]
    best = None
    for start in starts:
        x0 = start + [math.log(max(value, 1e-6) * 2.0) for value in peak_by_band]
        x0 = [min(max(value, lo + 1e-6), hi - 1e-6) for value, lo, hi in zip(x0, lower, upper)]
        try:
            solution = least_squares(residuals, x0, bounds=(lower, upper), method="trf", max_nfev=4000)
        except ValueError:
            continue
        if best is None or solution.cost < best.cost - 1e-9:
            best = solution
    if best is None or not best.success:
        return None, "the least-squares fit did not converge"
    dof = len(rows) - len(best.x)
    if dof <= 0:
        return None, f"{len(rows)} detections for {len(best.x)} parameters"
    jac = best.jac
    try:
        cov = np.linalg.inv(jac.T @ jac)
    except np.linalg.LinAlgError:
        return None, "the fit covariance is singular"
    if not np.all(np.isfinite(cov)) or np.any(np.diag(cov) < 0):
        return None, "the fit covariance is not positive"
    names = (["t0", "log_tau_rise", "log_tau_fall"] if model == "bazin"
             else ["t0", "log_gamma", "log_tau_rise", "log_tau_fall", "plateau_slope"]) + [f"log_amplitude_{band}" for band in bands]
    fit = _Fit(model, names, best.x, cov, float(2 * best.cost), dof, bands, tref)
    warnings = []
    at_bound = [names[i] for i, (value, lo, hi) in enumerate(zip(best.x, lower, upper)) if value - lo < 1e-4 or hi - value < 1e-4]
    if at_bound:
        warnings.append("parameters at a bound: " + ", ".join(at_bound))
    if span < 2 * math.exp(best.x[2 if model == "bazin" else 3]):
        warnings.append("the decline is longer than half the data span; decline-side values are extrapolated")
    return fit, warnings


def _fit_result(fit) -> dict[str, Any]:
    import numpy as np
    errors = np.sqrt(np.diag(fit.cov))
    params = {name: {"value": _round(float(value), 7), "sigma": _round(float(error), 4)}
              for name, value, error in zip(fit.names, fit.params, errors)}
    return {
        "referenceMjd": _round(fit.tref, 11),
        "parameters": params,
        "chi2": _round(fit.chi2, 6),
        "degreesOfFreedom": fit.dof,
        "reducedChi2": _round(fit.chi2 / fit.dof, 5),
        "aicc": _round(fit.aicc, 7),
        "bands": fit.bands,
    }


GRID = None


def _grid():
    import numpy as np
    global GRID
    if GRID is None:
        GRID = np.arange(-150.0, 400.0, 0.25)
    return GRID


def _shape_features(fit, params):
    """Timing of the shared shape: peak time, half-maximum rise, half-maximum duration and
    the 15-day decline (identical in every band, because only the amplitudes differ)."""

    import numpy as np
    grid = _grid()
    curve = fit.shape(params, grid)
    i = int(np.argmax(curve))
    if not math.isfinite(float(curve[i])) or curve[i] <= 0 or i in (0, len(grid) - 1):
        return None
    # parabolic refinement of the peak between grid samples
    y0, y1, y2 = float(curve[i - 1]), float(curve[i]), float(curve[i + 1])
    denominator = y0 - 2 * y1 + y2
    shift = 0.5 * (y0 - y2) / denominator if denominator < 0 else 0.0
    t_peak = float(grid[i]) + shift * (grid[1] - grid[0])
    peak = float(fit.shape(params, np.array([t_peak]))[0])
    half = peak / 2.0

    def crossing(indices, rising: bool) -> float | None:
        if not len(indices):
            return None
        k = int(indices[-1] if rising else indices[0])
        a, b = (k, k + 1) if rising else (k - 1, k)
        if a < 0 or b >= len(grid):
            return None
        fa, fb = float(curve[a]), float(curve[b])
        if fb == fa:
            return float(grid[a])
        return float(grid[a] + (half - fa) * (grid[b] - grid[a]) / (fb - fa))

    t_rise = crossing(np.where(curve[:i] < half)[0], rising=True)
    after = np.where(curve[i:] < half)[0]
    t_fall = crossing(after + i, rising=False) if len(after) else None
    late = float(fit.shape(params, np.array([t_peak + 15.0]))[0])
    decline = (-2.5 * math.log10(late / peak)) / 15.0 if late > 0 else None
    rise = (t_peak - t_rise) if t_rise is not None else None
    duration = (t_fall - t_rise) if (t_rise is not None and t_fall is not None) else None
    return t_peak, peak, rise, duration, decline


def features_from_fit(fit, rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
    import numpy as np
    nominal = _shape_features(fit, fit.params)
    if nominal is None:
        return None, ["the chosen model has no interior peak"]
    rng = np.random.default_rng(0)
    try:
        draws = rng.multivariate_normal(fit.params, fit.cov, size=MC_DRAWS, method="cholesky")
    except (np.linalg.LinAlgError, ValueError):
        draws = rng.multivariate_normal(fit.params, fit.cov, size=MC_DRAWS, method="eigh")
    samples = [(draw, value) for draw, value in ((draw, _shape_features(fit, draw)) for draw in draws) if value is not None]

    def spread(values: list[float]) -> float | None:
        values = [value for value in values if value is not None and math.isfinite(value)]
        if len(values) < MC_DRAWS // 2:
            return None
        low, high = np.percentile(values, [16, 84])
        return float((high - low) / 2)

    t_peak, peak, rise, duration, decline = nominal
    peak_mjd = fit.tref + t_peak
    warnings: list[str] = []
    bands: dict[str, Any] = {}
    n_shape = fit.n_shape()
    for position, band in enumerate(fit.bands):
        amplitude = math.exp(float(fit.params[n_shape + position]))
        peak_mag = ZP_UJY - 2.5 * math.log10(amplitude * peak)
        mags = [ZP_UJY - 2.5 * math.log10(math.exp(min(float(draw[n_shape + position]), 60.0)) * value[1])
                for draw, value in samples if value[1] > 0]
        band_times = [row["mjd"] for row in rows if row["band"] == band]
        bracketed = bool(band_times and min(band_times) < peak_mjd < max(band_times))
        if not bracketed:
            warnings.append(f"{band}: the peak lies outside the band's observed span (extrapolated)")
        bands[band] = {"peakMagnitude": _round(peak_mag, 5), "peakMagnitudeSigma": _round(spread(mags), 3),
                       "peakBracketedByData": bracketed}
    result = {
        "sharedShape": True,
        "peakMjd": _round(peak_mjd, 10), "peakMjdSigma": _round(spread([v[0] for _, v in samples]), 3),
        "halfMaxRiseDays": _round(rise, 4), "halfMaxRiseDaysSigma": _round(spread([v[2] for _, v in samples]), 3),
        "halfMaxDurationDays": _round(duration, 4), "halfMaxDurationDaysSigma": _round(spread([v[3] for _, v in samples]), 3),
        "declineMagPerDay15": _round(decline, 4), "declineMagPerDay15Sigma": _round(spread([v[4] for _, v in samples]), 3),
        "bands": bands,
    }
    return result, warnings


# ----------------------------------------------------------------------------- extinction
@lru_cache(maxsize=1)
def _sfd_maps(directory: str):
    from astropy.io import fits
    from astropy.wcs import WCS
    maps = {}
    for pole in ("ngp", "sgp"):
        path = Path(directory) / f"SFD_dust_4096_{pole}.fits"
        with fits.open(path, memmap=False) as hdul:
            maps[pole] = (hdul[0].data.astype("float64"), WCS(hdul[0].header))
    return maps


def sfd_directory() -> str | None:
    directory = os.environ.get("CTAS_SFD_DIR")
    if directory and all((Path(directory) / f"SFD_dust_4096_{pole}.fits").is_file() for pole in ("ngp", "sgp")):
        return directory
    return None


def sfd_ebv(ra_deg: float, dec_deg: float, directory: str) -> float:
    """SFD (1998) E(B-V) at a position, bilinear in the polar ZEA map pixels."""

    import numpy as np
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    galactic = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs").galactic
    pole = "ngp" if galactic.b.deg >= 0 else "sgp"
    data, wcs = _sfd_maps(directory)[pole]
    x, y = wcs.wcs_world2pix(np.array([[galactic.l.deg, galactic.b.deg]]), 0)[0]
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    fx, fy = x - x0, y - y0
    ny, nx = data.shape
    x0 = min(max(x0, 0), nx - 2)
    y0 = min(max(y0, 0), ny - 2)
    value = (data[y0, x0] * (1 - fx) * (1 - fy) + data[y0, x0 + 1] * fx * (1 - fy)
             + data[y0 + 1, x0] * (1 - fx) * fy + data[y0 + 1, x0 + 1] * fx * fy)
    return float(value)


def band_extinction(ebv_sfd: float, wavelength: float) -> float:
    import numpy as np
    import extinction
    a_v = R_V * SFD_RECALIBRATION * ebv_sfd
    return float(extinction.fitzpatrick99(np.array([wavelength]), a_v, R_V)[0])


# ----------------------------------------------------------------------------- runs
def absolute_magnitude_run(candidate: dict[str, Any], features: dict[str, Any] | None,
                           summary: dict[str, Any]) -> dict[str, Any]:
    redshift, source = None, None
    for key, label in (("redshift", "event redshift as retained (TNS)"), ("host_redshift", "host-galaxy redshift as retained")):
        try:
            value = float(candidate.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and 0 < value < 3:
            redshift, source = value, label
            break
    ra, dec = candidate.get("ra_deg"), candidate.get("dec_deg")
    inputs = {"photometryChecksumSha256": summary["inputChecksumSha256"], "redshift": _round(redshift, 6),
              "redshiftSource": source, "raDeg": _round(ra, 10) if ra is not None else None,
              "decDeg": _round(dec, 10) if dec is not None else None}
    parameters = {"cosmology": "Planck18 (astropy)", "extinctionMap": "SFD 1998, x0.86 (Schlafly & Finkbeiner 2011)",
                  "extinctionLaw": "Fitzpatrick 1999, R_V = 3.1", "kCorrection": "+2.5 log10(1+z) bandwidth term only; no spectral k-correction",
                  "bandEffectiveWavelengthsAngstrom": {band: BANDS[band][1] for band in BANDS}}
    references = ["1998ApJ...500..525S", "2011ApJ...737..103S", "1999PASP..111...63F", "2020A&A...641A...6P"]
    if not features:
        return _run("absolute-magnitude", "peak-absolute-magnitude", inputs=inputs, parameters=parameters,
                    reason="no peak magnitude (the light-curve features were not computed)", references=references)
    if redshift is None:
        return _run("absolute-magnitude", "peak-absolute-magnitude", inputs=inputs, parameters=parameters,
                    reason="no redshift is retained for this event", references=references)
    if ra is None or dec is None:
        return _run("absolute-magnitude", "peak-absolute-magnitude", inputs=inputs, parameters=parameters,
                    reason="no coordinates for the Galactic extinction", references=references)
    directory = sfd_directory()
    if directory is None:
        return _run("absolute-magnitude", "peak-absolute-magnitude", inputs=inputs, parameters=parameters,
                    reason="the SFD extinction map is not available to this build (CTAS_SFD_DIR)", references=references)
    try:
        import extinction  # noqa: F401
        from astropy.cosmology import Planck18
    except ImportError as exc:
        return _run("absolute-magnitude", "peak-absolute-magnitude", inputs=inputs, parameters=parameters,
                    reason=f"a required library is missing ({exc.name})", references=references)
    ebv = sfd_ebv(float(ra), float(dec), directory)
    mu = float(Planck18.distmod(redshift).value)
    k_term = 2.5 * math.log10(1 + redshift)
    bands = {}
    for band, entry in sorted((features.get("bands") or {}).items()):
        if entry.get("peakMagnitude") is None:
            continue
        a_band = band_extinction(ebv, BANDS[band][1])
        bands[band] = {"peakApparentMagnitude": entry["peakMagnitude"], "extinctionMag": _round(a_band, 4),
                       "peakAbsoluteMagnitude": _round(entry["peakMagnitude"] - mu - a_band + k_term, 5),
                       "peakAbsoluteMagnitudeSigma": entry.get("peakMagnitudeSigma")}
    result = {"redshift": _round(redshift, 6), "distanceModulus": _round(mu, 6), "ebvSfd": _round(ebv, 5),
              "ebvRecalibrated": _round(SFD_RECALIBRATION * ebv, 5), "kCorrectionTerm": _round(k_term, 5), "bands": bands}
    warnings = ["the absolute magnitude uses a redshift without its uncertainty; peculiar velocity and spectral k-correction are not included"]
    return _run("absolute-magnitude", "peak-absolute-magnitude", inputs=inputs, parameters=parameters, result=result,
                warnings=warnings, references=references,
                claim="A distance-dependent estimate from a phenomenological peak; not a calibrated luminosity.")


def jump_run(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    parameters = {"baselineWindowDays": [-70, -10], "latestWindowDays": 2, "minimumBaselineDetections": 3,
                  "jumpSigma": JUMP_SIGMA, "statistic": "(F_latest - mean F_baseline) / sqrt(sigma_latest^2 + sigma_mean^2)"}
    inputs = {"photometryChecksumSha256": summary["inputChecksumSha256"]}
    if not rows:
        return _run("jump-detector", "baseline-jump", inputs=inputs, parameters=parameters, reason="no fitted detections")
    latest = max(row["mjd"] for row in rows)
    bands: dict[str, Any] = {}
    for band in sorted({row["band"] for row in rows}):
        band_rows = [row for row in rows if row["band"] == band]
        recent = [row for row in band_rows if row["mjd"] >= latest - 2]
        base = [row for row in band_rows if latest - 70 <= row["mjd"] <= latest - 10]
        if not recent or len(base) < 3:
            continue
        def flux(row):
            value = 10 ** (-0.4 * (row["mag"] - ZP_UJY))
            return value, value * math.log(10) / 2.5 * row["err"]
        newest = max(recent, key=lambda row: row["mjd"])
        f_new, s_new = flux(newest)
        base_flux = np.array([flux(row) for row in base])
        weights = 1 / base_flux[:, 1] ** 2
        mean = float(np.sum(weights * base_flux[:, 0]) / np.sum(weights))
        sigma_mean = float(math.sqrt(1 / np.sum(weights)))
        significance = (f_new - mean) / math.sqrt(s_new ** 2 + sigma_mean ** 2)
        bands[band] = {"latestMjd": _round(newest["mjd"], 10), "baselineDetections": len(base),
                       "significance": _round(significance, 4), "jump": bool(significance >= JUMP_SIGMA)}
    if not bands:
        return _run("jump-detector", "baseline-jump", inputs=inputs, parameters=parameters,
                    reason="no band has a detection in the last 2 days and 3 detections 10-70 days earlier")
    result = {"latestMjd": _round(latest, 10), "bands": bands, "jump": any(entry["jump"] for entry in bands.values())}
    return _run("jump-detector", "baseline-jump", inputs=inputs, parameters=parameters, result=result,
                claim="A brightening relative to the event's own recent detections; not a classification.")


ALIAS_TOLERANCE = 0.02  # cycles per day around the 1, 2 and 3 day^-1 sampling aliases


def _model_mag(fit, band: str, mjd: float) -> float | None:
    import numpy as np
    if fit is None or band not in fit.bands:
        return None
    amplitude = math.exp(float(fit.params[fit.n_shape() + fit.bands.index(band)]))
    flux = amplitude * float(fit.shape(fit.params, np.array([mjd - fit.tref]))[0])
    return ZP_UJY - 2.5 * math.log10(flux) if flux > 0 else None


def periodogram_run(rows: list[dict[str, Any]], summary: dict[str, Any], fit=None) -> dict[str, Any]:
    import numpy as np
    parameters = {"minimumDetections": PERIOD_MIN_DETECTIONS, "minimumSpanDays": PERIOD_MIN_SPAN_DAYS,
                  "periodRangeDays": "0.1 to half the band's span", "samplesPerPeak": 10,
                  "falseAlarmMethod": "baluev", "periodicCandidateFap": PERIOD_FAP_THRESHOLD,
                  "detrending": "magnitudes minus the chosen multiband fit when one exists (a transient's own rise and fall is not a period)",
                  "periodicCandidateRule": ("FAP < 1e-3, best period shorter than a third of the span, and the frequency not within "
                                            "0.02 day^-1 of the 1, 2 or 3 day^-1 sampling aliases")}
    inputs = {"photometryChecksumSha256": summary["inputChecksumSha256"],
              "detrendedBy": ("multiband-" + fit.model) if fit is not None else None}
    try:
        from astropy.timeseries import LombScargle
    except ImportError:
        return _run("periodogram", "lomb-scargle", inputs=inputs, parameters=parameters, reason="astropy is not available")
    bands: dict[str, Any] = {}
    for band in sorted({row["band"] for row in rows}):
        band_rows = [row for row in rows if row["band"] == band]
        t = np.array([row["mjd"] for row in band_rows])
        if len(band_rows) < PERIOD_MIN_DETECTIONS or t.max() - t.min() < PERIOD_MIN_SPAN_DAYS:
            continue
        detrended = fit is not None and band in fit.bands
        mags = []
        for row in band_rows:
            model = _model_mag(fit, band, row["mjd"]) if detrended else None
            mags.append(row["mag"] - model if model is not None else (row["mag"] if not detrended else float("nan")))
        mag = np.array(mags)
        keep = np.isfinite(mag)
        if keep.sum() < PERIOD_MIN_DETECTIONS:
            continue
        t, mag = t[keep], mag[keep]
        err = np.array([row["err"] for row in band_rows])[keep]
        span = float(t.max() - t.min())
        model = LombScargle(t, mag, err)
        frequency, power = model.autopower(minimum_frequency=2.0 / span, maximum_frequency=10.0, samples_per_peak=10)
        k = int(np.argmax(power))
        best = float(frequency[k])
        fap = float(model.false_alarm_probability(power[k], method="baluev", minimum_frequency=2.0 / span, maximum_frequency=10.0))
        near_alias = any(abs(best - alias) < ALIAS_TOLERANCE for alias in (1.0, 2.0, 3.0))
        period = 1 / best
        bands[band] = {"detections": int(keep.sum()), "spanDays": _round(span, 5), "detrended": detrended,
                       "bestPeriodDays": _round(period, 6), "power": _round(float(power[k]), 5),
                       "falseAlarmProbability": _round(fap, 4), "nearSamplingAlias": near_alias,
                       "periodicCandidate": bool(fap < PERIOD_FAP_THRESHOLD and period < span / 3 and not near_alias)}
    if not bands:
        return _run("periodogram", "lomb-scargle", inputs=inputs, parameters=parameters,
                    reason=f"no band has {PERIOD_MIN_DETECTIONS} detections over {PERIOD_MIN_SPAN_DAYS:g} days")
    return _run("periodogram", "lomb-scargle", inputs=inputs, parameters=parameters,
                result={"bands": bands}, software={"astropy": _software().get("astropy", "")},
                references=["1976Ap&SS..39..447L", "1982ApJ...263..835S", "2008MNRAS.385.1279B"],
                claim=("A periodogram peak and its false-alarm probability under white noise, after removing the fitted transient; "
                       "residual structure of an imperfect fit can also produce peaks. Not a classification."))


SALT3_PHASE_WINDOW = (20.0, 50.0)
_BANDPASS_AVAILABLE: dict[str, bool] = {}


def _bandpass_available(name: str) -> bool:
    """Whether sncosmo can load a bandpass (checked once per build, so an unreachable
    remote bandpass is not retried for every row)."""

    if name not in _BANDPASS_AVAILABLE:
        try:
            import sncosmo
            sncosmo.get_bandpass(name)
            _BANDPASS_AVAILABLE[name] = True
        except Exception:  # noqa: BLE001 - unavailable to this build
            _BANDPASS_AVAILABLE[name] = False
    return _BANDPASS_AVAILABLE[name]


def ia_rule(candidate: dict[str, Any], broker_block: dict[str, Any] | None) -> tuple[bool, str]:
    label = str(candidate.get("classification") or "")
    if "Ia" in label.replace("Iax", "Ia"):
        return True, f"classified {label}"
    for broker in (broker_block or {}).get("brokers", []):
        reading = broker.get("reading") or {}
        if reading.get("coarseClass") == "SN Ia":
            return True, f"scored SN Ia by {broker.get('broker')} ({reading.get('rule')})"
    return False, ""


def salt3_run(candidate: dict[str, Any], rows: list[dict[str, Any]], summary: dict[str, Any],
              broker_block: dict[str, Any] | None) -> dict[str, Any] | None:
    eligible, why = ia_rule(candidate, broker_block)
    if not eligible:
        return None
    parameters = {"model": "salt3 (sncosmo built-in, latest version)", "fitter": "sncosmo.fit_lc (iminuit)",
                  "phaseWindowDays": "detections from 20 days before to 50 days after the brightest detection, times (1+z) (z = 0.1 when fitted)",
                  "minimumBands": 2, "bands": "ZTF g, r, i (ATLAS left out of the SALT3 fit)",
                  "bounds": {"x1": [-5, 5], "c": [-0.5, 1.0], "z": [0.005, 0.3]},
                  "fluxZeropointAB": ZP_UJY, "milkyWayDust": "not modelled (extinction reported separately)",
                  "selectionRule": "TNS classification containing 'Ia', or a broker reading of SN Ia"}
    inputs = {"photometryChecksumSha256": summary["inputChecksumSha256"], "selectedBecause": why}
    redshift = None
    try:
        value = float(candidate.get("redshift"))
        redshift = value if math.isfinite(value) and 0 < value < 1 else None
    except (TypeError, ValueError):
        pass
    inputs["fixedRedshift"] = _round(redshift, 6)
    try:
        import numpy as np
        import sncosmo
        from astropy.table import Table
    except ImportError as exc:
        return _run("salt3", "sncosmo-salt3", inputs=inputs, parameters=parameters, reason=f"a required library is missing ({exc.name})")
    data_dir = os.environ.get("CTAS_SNCOSMO_DATA_DIR")
    if data_dir:
        sncosmo.conf.data_dir = data_dir
    # ZTF g/r/i only: their bandpasses are pinned reference data (scripts/ctas_reference_data.sh);
    # ATLAS rows are left out of the SALT3 fit so it never depends on a third-party filter service.
    rows = [row for row in rows if row["band"].startswith("ztf")]
    if not rows:
        return _run("salt3", "sncosmo-salt3", inputs=inputs, parameters=parameters,
                    reason="no ZTF g/r/i detections (the SALT3 fit uses ZTF bands only)")
    missing = {band for band in {row["band"] for row in rows} if not _bandpass_available(BANDS[band][2])}
    peak_row = min(rows, key=lambda row: row["mag"])
    stretch = 1.0 + (redshift if redshift is not None else 0.1)
    window = (peak_row["mjd"] - SALT3_PHASE_WINDOW[0] * stretch, peak_row["mjd"] + SALT3_PHASE_WINDOW[1] * stretch)
    usable = [row for row in rows if row["band"] not in missing and window[0] <= row["mjd"] <= window[1]]
    used_bands = {row["band"] for row in usable}
    if len(usable) < 5 or len(used_bands) < 2:
        return _run("salt3", "sncosmo-salt3", inputs=inputs, parameters=parameters,
                    reason="fewer than 5 detections in two bands with an available bandpass inside the SALT3 phase window"
                    + (f" (bandpass unavailable: {', '.join(sorted(missing))})" if missing else ""))
    flux = [10 ** (-0.4 * (row["mag"] - ZP_UJY)) for row in usable]
    table = Table({"time": [row["mjd"] for row in usable], "band": [BANDS[row["band"]][2] for row in usable],
                   "flux": flux, "fluxerr": [f * math.log(10) / 2.5 * row["err"] for f, row in zip(flux, usable)],
                   "zp": [ZP_UJY] * len(usable), "zpsys": ["ab"] * len(usable)})
    try:
        model = sncosmo.Model(source="salt3")
    except Exception as exc:  # noqa: BLE001 - model files unavailable to this build
        return _run("salt3", "sncosmo-salt3", inputs=inputs, parameters=parameters,
                    reason=f"the SALT3 model files are not available to this build ({type(exc).__name__})")
    vary = ["t0", "x0", "x1", "c"]
    bounds = {"x1": (-5, 5), "c": (-0.5, 1.0)}
    if redshift is not None:
        model.set(z=redshift)
    else:
        vary = ["z"] + vary
        bounds["z"] = (0.005, 0.3)
    peak_guess = float(np.array(table["time"])[int(np.argmax(flux))])
    model.set(t0=peak_guess)
    bounds["t0"] = (peak_guess - 30, peak_guess + 30)
    warnings = []
    if missing:
        warnings.append("bands without an available bandpass were left out: " + ", ".join(sorted(missing)))
    try:
        result, fitted = sncosmo.fit_lc(table, model, vary, bounds=bounds, modelcov=False)
    except Exception as exc:  # noqa: BLE001 - the fit itself failed
        return _run("salt3", "sncosmo-salt3", inputs=inputs, parameters=parameters,
                    reason=f"the SALT3 fit failed ({type(exc).__name__})", warnings=warnings)
    values = dict(zip(result.param_names, result.parameters))
    errors = dict(result.errors) if result.errors else {}
    try:
        peak_b = float(fitted.source_peakmag("bessellb", "ab"))
    except Exception:  # noqa: BLE001 - the B bandpass is unavailable
        peak_b = None
        warnings.append("the rest-frame B peak magnitude was not computed (bandpass unavailable)")
    dof = int(result.ndof) if result.ndof else 0
    out = {"success": bool(result.success), "parameters": {name: {"value": _round(float(values[name]), 7),
                                                                   "sigma": _round(float(errors[name]), 4) if name in errors else None}
                                                            for name in ("z", "t0", "x0", "x1", "c")},
           "redshiftFitted": redshift is None, "chi2": _round(float(result.chisq), 6), "degreesOfFreedom": dof,
           "reducedChi2": _round(float(result.chisq) / dof, 5) if dof else None,
           "peakRestFrameBMagnitudeAB": _round(peak_b, 5), "bandsUsed": sorted({BANDS[row["band"]][2] for row in usable})}
    if not result.success:
        warnings.append("sncosmo reports the fit did not converge: " + str(result.message)[:160])
    return _run("salt3", "sncosmo-salt3", inputs=inputs, parameters=parameters, result=out, warnings=warnings,
                software={**_software(), "sncosmo": sncosmo.__version__},
                references=["2021ApJ...923..265K", "2016ascl.soft11017B"],
                claim="SALT3 light-curve parameters assuming a normal type Ia supernova; not a classification or a distance.")


def screens_run(candidate: dict[str, Any], features: dict[str, Any] | None, summary: dict[str, Any]) -> dict[str, Any]:
    parameters = {"fastRiseHalfMaxDays": FAST_RISE_DAYS, "fastDeclineMagPerDay": FAST_DECLINE_MAG_PER_DAY,
                  "nuclearOffsetArcsec": NUCLEAR_OFFSET_ARCSEC,
                  "rules": {"fast-transient": "shared-shape half-maximum rise < 3 d or 15-day decline > 0.3 mag/day",
                            "nuclear-transient": "a retained host offset < 0.5 arcsec"}}
    offsets = []
    for row in (candidate.get("follow_up") or {}).get("host_context") or []:
        try:
            value = float(row.get("transient_offset_arcsec"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0:
            offsets.append(value)
    inputs = {"photometryChecksumSha256": summary["inputChecksumSha256"],
              "hostOffsetsArcsec": sorted(_round(value, 5) for value in offsets)}
    result: dict[str, Any] = {}
    if features:
        drivers = []
        rise, decline = features.get("halfMaxRiseDays"), features.get("declineMagPerDay15")
        if rise is not None and rise < FAST_RISE_DAYS:
            drivers.append(f"half-maximum rise {rise:g} d")
        if decline is not None and decline > FAST_DECLINE_MAG_PER_DAY:
            drivers.append(f"15-day decline {decline:g} mag/day")
        result["fastTransient"] = {"flag": bool(drivers), "drivers": drivers}
    else:
        result["fastTransient"] = {"flag": None, "notComputedReason": "no light-curve features"}
    if offsets:
        nearest = min(offsets)
        result["nuclearTransient"] = {"flag": bool(nearest < NUCLEAR_OFFSET_ARCSEC), "nearestHostOffsetArcsec": _round(nearest, 5)}
    else:
        result["nuclearTransient"] = {"flag": None, "notComputedReason": "no retained host offset (host association is Track 3c part 2)"}
    return _run("screens", "timescale-and-offset-screens", inputs=inputs, parameters=parameters, result=result,
                claim="Screens that point at candidates for a closer look (kilonova-like timescales, nuclear position); they are not classifications.")


def build_lightcurve_science(candidate: dict[str, Any], broker_block: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The block for one candidate, or None when it has no fitted detection at all."""

    observations = (candidate.get("follow_up") or {}).get("observations") or []
    rows, excluded = usable_detections(observations)
    if not rows:
        return None
    summary = input_summary(rows, excluded)
    software = _software()
    runs: list[dict[str, Any]] = []
    per_band = summary["detectionsByBand"]
    good_bands = [band for band, count in per_band.items() if count >= MIN_BAND_DETECTIONS]
    span = rows[-1]["mjd"] - rows[0]["mjd"]
    fit_inputs = {"photometryChecksumSha256": summary["inputChecksumSha256"], "bands": good_bands}
    fit_rows = [row for row in rows if row["band"] in good_bands]
    reason = None
    if len(good_bands) < 2:
        reason = f"fewer than two bands with {MIN_BAND_DETECTIONS} detections"
    elif len(fit_rows) < MIN_TOTAL_DETECTIONS:
        reason = f"fewer than {MIN_TOTAL_DETECTIONS} detections in the fitted bands"
    elif span < MIN_SPAN_DAYS:
        reason = f"the detections span less than {MIN_SPAN_DAYS:g} days"
    fits = {}
    for model, method, refs, formula in (
        ("bazin", "multiband-bazin", ["2009A&A...499..653B"], "F_b(t) = A_b exp(-(t-t0)/tau_fall) / (1 + exp(-(t-t0)/tau_rise))"),
        ("villar", "multiband-villar", ["2019ApJ...884...83V"],
         "F_b(t) = A_b (1 + s(t-t0)) / (1 + exp(-(t-t0)/tau_rise)) for t < t0+gamma; A_b (1 + s gamma) exp(-(t-t0-gamma)/tau_fall) / (1 + exp(-(t-t0)/tau_rise)) after"),
    ):
        parameters = {"model": formula, "sharedParameters": "timing (and the plateau slope s) shared across bands; one amplitude per band",
                      "space": "flux (microjansky, AB zero point 23.9)", "fitter": "scipy.optimize.least_squares (trf), deterministic starts",
                      "minimumBandDetections": MIN_BAND_DETECTIONS, "minimumDetections": MIN_TOTAL_DETECTIONS, "minimumSpanDays": MIN_SPAN_DAYS}
        if reason:
            runs.append(_run(method, method, inputs=fit_inputs, parameters=parameters, reason=reason, references=refs))
            continue
        try:
            fit, info = fit_model(model, fit_rows)
        except Exception as exc:  # noqa: BLE001 - reported as not computed, see guarded() below
            fit, info = None, f"the fit raised {type(exc).__name__}; reported, not hidden"
        if fit is None:
            runs.append(_run(method, method, inputs=fit_inputs, parameters=parameters, reason=str(info), references=refs))
            continue
        fits[model] = fit
        runs.append(_run(method, method, inputs=fit_inputs, parameters=parameters, result=_fit_result(fit), warnings=info,
                         software=software, references=refs))
    features = None
    feature_parameters = {"draws": MC_DRAWS, "seed": 0, "declineWindowDays": 15,
                          "modelChoice": "the multiband fit with the lower AICc",
                          "colourScreen": "the per-epoch colour and blackbody temperature are the collector's multiband screen under analysis_screens"}
    if fits:
        chosen = min(fits.values(), key=lambda item: (item.aicc, item.model))
        try:
            features, feature_warnings = features_from_fit(chosen, fit_rows)
        except Exception as exc:  # noqa: BLE001
            features, feature_warnings = None, [f"the feature extraction raised {type(exc).__name__}"]
        feature_inputs = {**fit_inputs, "model": "multiband-" + chosen.model}
        if features:
            runs.append(_run("lightcurve-features", "fit-derived-features", inputs=feature_inputs, parameters=feature_parameters,
                             result={"model": "multiband-" + chosen.model, **features}, warnings=feature_warnings, software=software))
        else:
            runs.append(_run("lightcurve-features", "fit-derived-features", inputs=feature_inputs, parameters=feature_parameters,
                             reason="the chosen model has no interior peak in any band", warnings=feature_warnings))
            features = None
    else:
        runs.append(_run("lightcurve-features", "fit-derived-features", inputs=fit_inputs, parameters=feature_parameters,
                         reason="no multiband fit"))
    def guarded(kind: str, method: str, compute):
        # One analysis failing must not stop the others or the release: the run is published
        # as not computed, naming the error type, and the gate still checks it.
        try:
            return compute()
        except Exception as exc:  # noqa: BLE001
            return _run(kind, method, inputs={"photometryChecksumSha256": summary["inputChecksumSha256"]},
                        parameters={}, reason=f"the analysis raised {type(exc).__name__}; reported, not hidden")

    runs.append(guarded("absolute-magnitude", "peak-absolute-magnitude", lambda: absolute_magnitude_run(candidate, features, summary)))
    runs.append(guarded("jump-detector", "baseline-jump", lambda: jump_run(rows, summary)))
    chosen_fit = min(fits.values(), key=lambda item: (item.aicc, item.model)) if fits else None
    runs.append(guarded("periodogram", "lomb-scargle", lambda: periodogram_run(rows, summary, chosen_fit)))
    salt = guarded("salt3", "sncosmo-salt3", lambda: salt3_run(candidate, fit_rows or rows, summary, broker_block))
    if salt is not None:
        runs.append(salt)
    runs.append(guarded("screens", "timescale-and-offset-screens", lambda: screens_run(candidate, features, summary)))
    return {"schema": SCHEMA, "inputs": summary, "runs": runs}


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def lightcurve_problems(candidates: Iterable[dict[str, Any]]) -> list[str]:
    """Certificate gate: every run names its method version and inputs; complete runs carry
    a result checksum that matches; not-computed runs say why."""

    problems: list[str] = []
    for candidate in candidates:
        block = candidate.get(CANDIDATE_KEY)
        if block is None:
            continue
        name = candidate.get("name") or candidate.get("event_id")
        if not isinstance(block, dict) or block.get("schema") != SCHEMA or not isinstance(block.get("runs"), list):
            problems.append(f"{name}: {CANDIDATE_KEY} is not a {SCHEMA} block")
            continue
        if not _valid_sha256((block.get("inputs") or {}).get("inputChecksumSha256")):
            problems.append(f"{name}: photometry input checksum missing")
        for run in block["runs"]:
            label = f"{name} {run.get('analysisType')}"
            if run.get("analysisType") not in RUN_TYPES:
                problems.append(f"{label}: unknown run type")
            if not run.get("methodName") or not run.get("methodVersion"):
                problems.append(f"{label}: method name or version missing")
            if not isinstance(run.get("inputs"), dict) or not run["inputs"] or run.get("inputChecksumSha256") != _sha256(run["inputs"]):
                problems.append(f"{label}: inputs or input checksum missing or wrong")
            if not run.get("claimBoundary"):
                problems.append(f"{label}: claim boundary missing")
            if run.get("status") == "complete":
                if run.get("resultChecksumSha256") != _sha256(run.get("result")):
                    problems.append(f"{label}: result checksum missing or wrong")
            elif run.get("status") == "not-computed":
                if not run.get("notComputedReason") or "result" in run:
                    problems.append(f"{label}: a not-computed run must give a reason and no result")
            else:
                problems.append(f"{label}: unknown status")
    return problems


def _build_one(args: tuple[dict[str, Any], dict[str, Any] | None]) -> dict[str, Any] | None:
    import warnings
    candidate, broker_block = args
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return build_lightcurve_science(candidate, broker_block)


def build_all(candidates: list[dict[str, Any]], broker_blocks: dict[str, dict[str, Any]] | None = None,
              workers: int | None = None) -> dict[str, dict[str, Any] | None]:
    """Blocks for every candidate with photometry, fitted in worker processes. The result
    does not depend on the number of workers (each event's fit is independent and seeded)."""

    broker_blocks = broker_blocks or {}
    jobs = []
    for candidate in candidates:
        observations = (candidate.get("follow_up") or {}).get("observations")
        if not observations:
            continue
        slim = {key: candidate.get(key) for key in ("event_id", "name", "classification", "redshift", "host_redshift",
                                                     "ra_deg", "dec_deg")}
        slim["follow_up"] = {"observations": observations,
                             "host_context": (candidate.get("follow_up") or {}).get("host_context") or []}
        jobs.append((str(candidate["event_id"]), slim, broker_blocks.get(str(candidate["event_id"]))))
    if workers is None:
        workers = int(os.environ.get("CTAS_LIGHTCURVE_WORKERS") or min(4, os.cpu_count() or 1))
    results: dict[str, dict[str, Any] | None] = {}
    if workers <= 1 or len(jobs) < 50:
        for event_id, slim, broker in jobs:
            results[event_id] = _build_one((slim, broker))
        return results
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    context = multiprocessing.get_context("fork") if hasattr(os, "fork") else None
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
        for (event_id, _slim, _broker), block in zip(jobs, pool.map(_build_one, [(slim, broker) for _, slim, broker in jobs], chunksize=8)):
            results[event_id] = block
    return results
