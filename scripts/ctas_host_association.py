"""Host association and counterpart context for CTAS dossiers (Framework v2 Track 3c, part 2).

Every public event with something to say gets a ``host_association`` block
(``ctas.host-association@1``): analysis runs that each name their method and version, their
exact inputs (with a checksum), their parameters, their result (with a checksum), warnings
and a claim boundary; a run that cannot be computed says why and carries no default value.

Runs:

* ``host-posterior``: a Prost-style probabilistic ranking (after Gagliano et al. 2021, GHOST,
  and Gagliano et al., astro-prost) of the galaxies CTAS already retains around the event
  (NED Object Directory cone, SIMBAD cone, Gaia DR3 galaxy candidates), plus an explicit
  "host missed" hypothesis. This is a reimplementation over retained rows, not astro-prost
  itself: astro-prost queries GLADE+, DECaLS and Pan-STARRS live, which the exporter does not
  do. Each candidate's offset is made dimensionless in one of three stated ways, in order of
  preference: by the directional light radius from the SIMBAD shape (Gupta et al. 2016); by a
  stated physical scale at the candidate's redshift (Planck 2018 angular-diameter distance);
  or by a stated angular scale. The offset term is exp(-f), the redshift term (only when the
  transient has a retained redshift and the candidate one) a Gaussian in the difference, the
  "missed" term a candidate at a stated fractional offset. Posteriors are normalised over the
  candidates and "missed". Nothing is averaged, nothing replaces the reported host fields.
* ``prior-activity``: retained detections (ATLAS forced photometry, ZTF data-release light
  curves, any provider) well before discovery, with the pre-discovery coverage, and a flag
  under a stated rule.
* ``extinction-and-point-source``: the SFD (1998) Galactic E(B-V) at the position (the
  pinned map of part 1, recalibrated x 0.86) with a stated high-extinction threshold, and a
  Gaia DR3 point-source flag (a star-like Gaia source at the position). Pan-STARRS
  point-source classifications are not retained, and the run says so.
* ``stellar-counterpart``: for a Gaia DR3 source at the position with a significant
  parallax, its absolute G magnitude and BP-RP colour for a colour-magnitude plot (no
  extinction correction, stated).

The block also names a Blast (SCIMMA) host-SED page for TNS-named events; it is a link, not
data CTAS holds.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable

CANDIDATE_KEY = "host_association"
SCHEMA = "ctas.host-association@1"
RUN_TYPES = ("host-posterior", "prior-activity", "extinction-and-point-source", "stellar-counterpart")
VERSION = "1.0.0"

# Host-posterior parameters (stated in every run)
MERGE_RADIUS_ARCSEC = 2.0
PHYSICAL_SCALE_KPC = 3.5
ANGULAR_SCALE_ARCSEC = 5.0
MAX_FRACTIONAL_OFFSET = 10.0
MISSED_FRACTIONAL_OFFSET = 3.0
REDSHIFT_FLOOR = 0.003  # ~900 km/s: peculiar velocities and redshift-definition differences
PROBABLE_POSTERIOR = 0.5
PROBABLE_RATIO = 2.0
NED_GALAXY_TYPES = {"G", "GPair", "GTrpl", "GGroup", "GClstr", "PofG", "QSO"}
SIMBAD_GALAXY_TYPES = {
    "G", "GiC", "GiG", "GiP", "BiC", "IG", "PaG", "SBG", "SyG", "Sy1", "Sy2", "AGN", "LIN", "EmG",
    "H2G", "bCG", "LSB", "rG", "QSO", "Bla", "BLL", "BrClG", "GrG", "CGG", "ClG", "PCG", "SCG",
}
GAIA_GALAXY_PROBABILITY = 0.5
NED_CONE_ARCSEC = 60.0
SIMBAD_CONE_ARCSEC = 30.0
GAIA_CONE_ARCSEC = 3.0

# Prior activity
PRIOR_GAP_DAYS = 30.0
PRIOR_MAX_MAG_ERROR = 0.217  # signal-to-noise >= 5
PRIOR_MIN_DETECTIONS = 2
PRIOR_MIN_NIGHTS = 2

# Extinction and point source
HIGH_EBV = 0.3  # recalibrated E(B-V), i.e. A_V ~ 0.93 mag
POINT_SOURCE_RADIUS_ARCSEC = 1.5
STAR_PROBABILITY = 0.9
PARALLAX_SIGNIFICANCE = 5.0
MOTION_SIGNIFICANCE = 5.0
SFD_RECALIBRATION = 0.86

TNS_NAME = re.compile(r"^(?:SN|AT|TDE|FRB|Kilonova|LRN)?\s*(\d{4}[a-z]{1,4})$")
BLAST_URL = "https://blast.scimma.org/transients/"
CLAIM_POSTERIOR = (
    "A ranking of retained catalogued galaxies under stated priors; the most probable "
    "candidate is a suggestion for follow-up, not a host identification, and a host fainter "
    "than the catalogues or outside their cones is always possible."
)
CLAIM_PRIOR = ("Retained earlier detections at the position; they show earlier variability at "
               "the position, not that the same source caused both.")
CLAIM_EXT = ("Galactic foreground extinction and a star-like Gaia source at the position; "
             "context for reading the photometry, not a classification.")
CLAIM_STAR = ("The position of a Gaia DR3 source at the transient position on a "
              "colour-magnitude plot; it is not proof that the star is the transient.")
REFERENCES_POSTERIOR = ["2021ApJ...908..170G", "2016AJ....152..154G", "2020A&A...641A...6P"]


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


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _run(kind: str, method: str, *, inputs: dict[str, Any], parameters: dict[str, Any],
         result: dict[str, Any] | None = None, reason: str | None = None,
         warnings: list[str] | None = None, claim: str, references: list[str] | None = None) -> dict[str, Any]:
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
    if result is not None:
        run["result"] = result
        run["resultChecksumSha256"] = _sha256(result)
    else:
        run["notComputedReason"] = reason or "not computed"
    return run


def separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    a = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a)))) * 3600.0


def position_angle_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Position angle (east of north) of point 2 seen from point 1."""

    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    y = math.sin(r2 - r1) * math.cos(d2)
    x = math.cos(d1) * math.sin(d2) - math.sin(d1) * math.cos(d2) * math.cos(r2 - r1)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def directional_light_radius(major_arcsec: float, minor_arcsec: float, galaxy_pa_deg: float,
                             transient_pa_deg: float) -> float:
    """Gupta et al. (2016): the radius of the galaxy's ellipse in the transient's direction."""

    a, b = major_arcsec / 2.0, minor_arcsec / 2.0
    phi = math.radians(transient_pa_deg - galaxy_pa_deg)
    return a * b / math.sqrt((a * math.sin(phi)) ** 2 + (b * math.cos(phi)) ** 2)


_PLANCK18 = None


def angular_diameter_kpc_per_arcsec(z: float) -> float | None:
    global _PLANCK18
    if z is None or z <= 0:
        return None
    try:
        if _PLANCK18 is None:
            from astropy.cosmology import Planck18
            _PLANCK18 = Planck18
        return float(_PLANCK18.kpc_proper_per_arcmin(z).value) / 60.0
    except Exception:  # pragma: no cover - astropy is installed wherever the exporter runs
        return None


# ----------------------------------------------------------------------------- candidates
def galaxy_candidates(candidate: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Retained catalogued galaxies around the event, merged across catalogues by position."""

    ra, dec = _float(candidate.get("ra_deg")), _float(candidate.get("dec_deg"))
    rows = ((candidate.get("follow_up") or {}).get("catalog_counterparts")) or []
    singles: list[dict[str, Any]] = []
    considered: dict[str, int] = {}
    for row in rows:
        catalog = str(row.get("catalog") or "")
        flags = _json(row.get("quality_flags"))
        gra, gdec = _float(row.get("ra_deg")), _float(row.get("dec_deg"))
        if gra is None or gdec is None:
            continue
        entry: dict[str, Any] | None = None
        if catalog == "NEDTAP.objdir":
            code = str(flags.get("object_type_code") or "")
            if code in NED_GALAXY_TYPES:
                entry = {"catalog": "NED", "name": flags.get("preferred_name") or row.get("catalog_record_id"),
                         "type": code, "redshift": _float(flags.get("redshift")),
                         "redshiftError": _float(flags.get("redshift_error")),
                         "redshiftReference": flags.get("redshift_bibcode"), "redshiftFlag": flags.get("redshift_flag")}
        elif catalog == "simbad.basic":
            code = str(row.get("counterpart_type") or "")
            if code in SIMBAD_GALAXY_TYPES:
                entry = {"catalog": "SIMBAD", "name": row.get("catalog_record_id"), "type": code,
                         "redshift": _float(flags.get("redshift")) if str(flags.get("redshift_type") or "z") == "z" else None,
                         "redshiftError": _float(flags.get("redshift_error")),
                         "redshiftReference": flags.get("redshift_bibcode"),
                         "majorAxisArcsec": None if _float(flags.get("major_axis_arcmin")) is None else _float(flags.get("major_axis_arcmin")) * 60.0,
                         "minorAxisArcsec": None if _float(flags.get("minor_axis_arcmin")) is None else _float(flags.get("minor_axis_arcmin")) * 60.0,
                         "positionAngleDeg": _float(flags.get("position_angle_deg")),
                         "shapeReference": flags.get("dimensions_bibcode")}
        elif catalog == "I/355/gaiadr3":
            probability = _float(flags.get("class_probability_galaxy"))
            if probability is not None and probability >= GAIA_GALAXY_PROBABILITY:
                entry = {"catalog": "Gaia DR3", "name": "Gaia DR3 " + str(row.get("catalog_record_id")), "type": "galaxy candidate",
                         "gaiaGalaxyProbability": probability}
        if entry is None:
            continue
        considered[entry["catalog"]] = considered.get(entry["catalog"], 0) + 1
        entry.update({"ra": gra, "dec": gdec, "assertionId": row.get("assertion_id"),
                      "responseChecksum": row.get("response_checksum"), "sourceUrl": row.get("source_url")})
        singles.append(entry)
    singles.sort(key=lambda e: ({"SIMBAD": 0, "NED": 1, "Gaia DR3": 2}[e["catalog"]], str(e.get("name")), str(e.get("assertionId"))))
    merged: list[dict[str, Any]] = []
    for entry in singles:
        home = next((m for m in merged if separation_arcsec(m["ra"], m["dec"], entry["ra"], entry["dec"]) <= MERGE_RADIUS_ARCSEC), None)
        if home is None:
            merged.append({"name": entry["name"], "ra": entry["ra"], "dec": entry["dec"], "entries": [entry]})
        else:
            home["entries"].append(entry)
    if ra is None or dec is None:
        return [], considered
    out = []
    for group in merged:
        entries = group["entries"]
        shape = next((e for e in entries if e.get("majorAxisArcsec") and e.get("minorAxisArcsec") and e.get("positionAngleDeg") is not None), None)
        redshifts = [{"value": e["redshift"], "error": e.get("redshiftError"), "catalog": e["catalog"],
                      "reference": e.get("redshiftReference")} for e in entries if e.get("redshift") is not None]
        out.append({
            "name": str(group["name"]),
            "raDeg": _round(group["ra"], 9), "decDeg": _round(group["dec"], 9),
            "catalogs": sorted({e["catalog"] for e in entries}),
            "types": sorted({str(e.get("type")) for e in entries}),
            "assertionIds": sorted(str(e.get("assertionId")) for e in entries if e.get("assertionId")),
            "redshifts": redshifts,
            "shape": None if shape is None else {"majorAxisArcsec": _round(shape["majorAxisArcsec"]), "minorAxisArcsec": _round(shape["minorAxisArcsec"]),
                                                  "positionAngleDeg": _round(shape["positionAngleDeg"]), "reference": shape.get("shapeReference")},
            "sourceUrl": next((e.get("sourceUrl") for e in entries if e.get("sourceUrl")), None),
            "_ra": group["ra"], "_dec": group["dec"],
        })
    return out, considered


def transient_redshift(candidate: dict[str, Any]) -> tuple[float | None, float | None]:
    """The transient's own retained redshift (not a host redshift)."""

    value = _float(candidate.get("redshift"))
    if value is None or value <= 0:
        return None, None
    return value, _float(candidate.get("redshift_error"))


def host_posterior_run(candidate: dict[str, Any]) -> dict[str, Any]:
    ra, dec = _float(candidate.get("ra_deg")), _float(candidate.get("dec_deg"))
    galaxies, considered = galaxy_candidates(candidate)
    z_t, z_t_err = transient_redshift(candidate)
    parameters = {
        "mergeRadiusArcsec": MERGE_RADIUS_ARCSEC, "physicalScaleKpc": PHYSICAL_SCALE_KPC,
        "angularScaleArcsec": ANGULAR_SCALE_ARCSEC, "maxFractionalOffset": MAX_FRACTIONAL_OFFSET,
        "missedFractionalOffset": MISSED_FRACTIONAL_OFFSET, "redshiftFloor": REDSHIFT_FLOOR,
        "probablePosterior": PROBABLE_POSTERIOR, "probableRatio": PROBABLE_RATIO,
        "cones": {"NED": NED_CONE_ARCSEC, "SIMBAD": SIMBAD_CONE_ARCSEC, "Gaia DR3": GAIA_CONE_ARCSEC},
        "rules": {
            "offset": "f = separation / directional light radius (SIMBAD shape), else separation x kpc/arcsec(z) / physical scale, else separation / angular scale; term exp(-f); f > maxFractionalOffset gives 0",
            "redshift": "only when the transient and the candidate both have a redshift: exp(-0.5 (dz / sigma)^2), sigma^2 = sum of the stated errors^2 + floor^2",
            "missed": "a host not in the retained catalogues counts as one candidate at f = missedFractionalOffset",
            "probable": "the top candidate is 'probable' when its posterior >= probablePosterior and >= probableRatio x the next hypothesis",
        },
    }
    inputs = {"transientRaDeg": _round(ra, 9), "transientDecDeg": _round(dec, 9),
              "transientRedshift": _round(z_t), "transientRedshiftError": _round(z_t_err),
              "candidateAssertionIds": sorted(a for g in galaxies for a in g["assertionIds"]),
              "candidateRowsByCatalog": dict(sorted(considered.items()))}
    if ra is None or dec is None:
        return _run("host-posterior", "prost-style-retained-catalog-ranking", inputs=inputs, parameters=parameters,
                    reason="the event has no position", claim=CLAIM_POSTERIOR, references=REFERENCES_POSTERIOR)
    if not galaxies:
        return _run("host-posterior", "prost-style-retained-catalog-ranking", inputs=inputs, parameters=parameters,
                    reason="no catalogued galaxy is retained around the event (NED/SIMBAD/Gaia cones)", claim=CLAIM_POSTERIOR,
                    references=REFERENCES_POSTERIOR)
    warnings: list[str] = []
    scored = []
    for galaxy in galaxies:
        sep = separation_arcsec(ra, dec, galaxy["_ra"], galaxy["_dec"])
        basis, scale, f = None, None, None
        shape = galaxy["shape"]
        if shape:
            pa_t = position_angle_deg(galaxy["_ra"], galaxy["_dec"], ra, dec)
            dlr = directional_light_radius(shape["majorAxisArcsec"], shape["minorAxisArcsec"], shape["positionAngleDeg"], pa_t)
            if dlr > 0:
                basis, scale, f = "directional-light-radius", dlr, sep / dlr
        if f is None:
            z_values = [r["value"] for r in galaxy["redshifts"] if r["value"] and r["value"] > 0]
            if z_values:
                z_g = sorted(z_values)[len(z_values) // 2]
                kpc = angular_diameter_kpc_per_arcsec(z_g)
                if kpc:
                    basis, scale, f = "physical-scale", kpc, sep * kpc / PHYSICAL_SCALE_KPC
        if f is None:
            basis, scale, f = "angular-scale", ANGULAR_SCALE_ARCSEC, sep / ANGULAR_SCALE_ARCSEC
        offset_term = math.exp(-f) if f <= MAX_FRACTIONAL_OFFSET else 0.0
        z_term, dz = 1.0, None
        if z_t is not None and galaxy["redshifts"]:
            best = min(galaxy["redshifts"], key=lambda r: abs(r["value"] - z_t))
            sigma = math.sqrt((z_t_err or 0.0) ** 2 + (best.get("error") or 0.0) ** 2 + REDSHIFT_FLOOR ** 2)
            dz = best["value"] - z_t
            z_term = math.exp(-0.5 * (dz / sigma) ** 2)
        offset_kpc = None
        if basis == "physical-scale":
            offset_kpc = sep * scale
        scored.append((galaxy, sep, basis, scale, f, offset_term * z_term, dz, offset_kpc))
    missed_weight = math.exp(-MISSED_FRACTIONAL_OFFSET)
    total = sum(item[5] for item in scored) + missed_weight
    ranked = sorted(scored, key=lambda item: (-item[5], item[1], item[0]["name"]))
    candidates_out = []
    for rank, (galaxy, sep, basis, scale, f, weight, dz, offset_kpc) in enumerate(ranked, start=1):
        candidates_out.append({
            "rank": rank, "name": galaxy["name"], "catalogs": galaxy["catalogs"], "types": galaxy["types"],
            "raDeg": galaxy["raDeg"], "decDeg": galaxy["decDeg"], "separationArcsec": _round(sep),
            "offsetBasis": basis, "offsetScale": _round(scale), "fractionalOffset": _round(f),
            "offsetKpc": _round(offset_kpc), "redshifts": galaxy["redshifts"], "redshiftDifference": _round(dz),
            "shape": galaxy["shape"], "assertionIds": galaxy["assertionIds"], "sourceUrl": galaxy["sourceUrl"],
            "posterior": _round(weight / total, 4),
        })
    missed = _round(missed_weight / total, 4)
    top = candidates_out[0]
    second = max([c["posterior"] for c in candidates_out[1:]] + [missed])
    if missed >= top["posterior"]:
        verdict = "host-missed-most-probable"
    elif top["posterior"] >= PROBABLE_POSTERIOR and top["posterior"] >= PROBABLE_RATIO * second:
        verdict = "probable"
    else:
        verdict = "ambiguous"
    if any(c["offsetBasis"] == "angular-scale" for c in candidates_out):
        warnings.append("some candidates have neither a retained shape nor a redshift; their offsets use the stated angular scale")
    if z_t is None:
        warnings.append("the transient has no retained redshift; the redshift term was not used")
    result = {"verdict": verdict, "candidates": candidates_out, "hostMissedPosterior": missed,
              "probableHost": top["name"] if verdict == "probable" else None,
              "probableHostOffsetArcsec": top["separationArcsec"] if verdict == "probable" else None}
    return _run("host-posterior", "prost-style-retained-catalog-ranking", inputs=inputs, parameters=parameters, result=result,
                warnings=warnings, claim=CLAIM_POSTERIOR, references=REFERENCES_POSTERIOR)


# ----------------------------------------------------------------------------- prior activity
def _mjd_of(value: Any) -> float | None:
    if value is None:
        return None
    try:
        from datetime import datetime, timezone
        text = str(value).replace("Z", "+00:00")
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp() / 86400.0 + 40587.0
    except ValueError:
        return None


def prior_activity_run(candidate: dict[str, Any]) -> dict[str, Any] | None:
    observations = ((candidate.get("follow_up") or {}).get("observations")) or []
    discovery = _mjd_of(candidate.get("discovery_time"))
    parameters = {"gapDays": PRIOR_GAP_DAYS, "maxMagnitudeError": PRIOR_MAX_MAG_ERROR,
                  "minDetections": PRIOR_MIN_DETECTIONS, "minNights": PRIOR_MIN_NIGHTS,
                  "rule": "prior activity = at least minDetections detections (S/N >= 5) on at least minNights nights more than gapDays before discovery"}
    if not observations:
        return None
    if discovery is None:
        inputs = {"discoveryTime": candidate.get("discovery_time"), "observationRows": len(observations)}
        return _run("prior-activity", "pre-discovery-history", inputs=inputs, parameters=parameters,
                    reason="the event has no discovery time", claim=CLAIM_PRIOR)
    cutoff = discovery - PRIOR_GAP_DAYS
    epochs, detections = [], []
    for row in observations:
        if row.get("superseded"):
            continue
        mjd = _float(row.get("mjd"))
        if mjd is None or mjd >= cutoff:
            continue
        key = {"assertionId": row.get("assertion_id"), "provider": row.get("provider"), "mjd": _round(mjd, 9),
               "band": row.get("original_band") or row.get("band"), "detection": bool(row.get("detection"))}
        epochs.append(key)
        err = _float(row.get("magnitude_error"))
        if row.get("detection") and _float(row.get("magnitude")) is not None and err is not None and err <= PRIOR_MAX_MAG_ERROR:
            detections.append({**key, "magnitude": _round(_float(row.get("magnitude"))), "magnitudeError": _round(err)})
    epochs.sort(key=lambda e: (e["mjd"], str(e["provider"]), str(e["assertionId"])))
    detections.sort(key=lambda e: (e["mjd"], str(e["provider"]), str(e["assertionId"])))
    inputs = {"discoveryMjd": _round(discovery, 9), "cutoffMjd": _round(cutoff, 9),
              "preDiscoveryEpochIds": [str(e["assertionId"]) for e in epochs]}
    if not epochs:
        return _run("prior-activity", "pre-discovery-history", inputs=inputs, parameters=parameters,
                    reason="no retained photometry more than 30 days before discovery", claim=CLAIM_PRIOR)
    nights = sorted({math.floor(e["mjd"]) for e in detections})
    covered_nights = sorted({math.floor(e["mjd"]) for e in epochs})
    providers = sorted({str(e["provider"]) for e in epochs})
    result = {
        "flag": len(detections) >= PRIOR_MIN_DETECTIONS and len(nights) >= PRIOR_MIN_NIGHTS,
        "preDiscoveryEpochs": len(epochs), "preDiscoveryNights": len(covered_nights),
        "earliestMjd": epochs[0]["mjd"], "latestMjd": epochs[-1]["mjd"], "providers": providers,
        "detections": detections[:50], "detectionCount": len(detections), "detectionNights": len(nights),
    }
    warnings = ["only the first 50 prior detections are listed"] if len(detections) > 50 else []
    return _run("prior-activity", "pre-discovery-history", inputs=inputs, parameters=parameters, result=result,
                warnings=warnings, claim=CLAIM_PRIOR)


# ----------------------------------------------------------------------------- extinction / point source / CMD
def _gaia_rows_at_position(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    ra, dec = _float(candidate.get("ra_deg")), _float(candidate.get("dec_deg"))
    out = []
    if ra is None or dec is None:
        return out
    for row in ((candidate.get("follow_up") or {}).get("catalog_counterparts")) or []:
        if row.get("catalog") != "I/355/gaiadr3":
            continue
        gra, gdec = _float(row.get("ra_deg")), _float(row.get("dec_deg"))
        if gra is None or gdec is None:
            continue
        sep = separation_arcsec(ra, dec, gra, gdec)
        if sep <= POINT_SOURCE_RADIUS_ARCSEC:
            out.append((sep, row))
    out.sort(key=lambda item: (item[0], str(item[1].get("catalog_record_id"))))
    return out


def _star_like(row: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    flags, motion = _json(row.get("quality_flags")), _json(row.get("motion"))
    reasons = []
    star = _float(flags.get("class_probability_star"))
    if star is not None and star >= STAR_PROBABILITY:
        reasons.append(f"Gaia star probability {star:g}")
    plx, plx_err = _float(motion.get("parallax_mas")), _float(motion.get("parallax_error_mas"))
    if plx is not None and plx_err and plx / plx_err >= PARALLAX_SIGNIFICANCE:
        reasons.append(f"parallax {plx:g} ± {plx_err:g} mas")
    pmra, pmra_err = _float(motion.get("pmra_mas_per_year")), _float(motion.get("pmra_error_mas_per_year"))
    pmdec, pmdec_err = _float(motion.get("pmdec_mas_per_year")), _float(motion.get("pmdec_error_mas_per_year"))
    if None not in (pmra, pmdec) and pmra_err and pmdec_err:
        significance = math.hypot(pmra / pmra_err, pmdec / pmdec_err)
        if significance >= MOTION_SIGNIFICANCE:
            reasons.append(f"proper motion at {significance:.1f} sigma")
    return bool(reasons), reasons, {"flags": flags, "motion": motion}


def extinction_point_source_run(candidate: dict[str, Any], sfd_directory: str | None, sfd_ebv) -> dict[str, Any] | None:
    ra, dec = _float(candidate.get("ra_deg")), _float(candidate.get("dec_deg"))
    if ra is None or dec is None:
        return None
    gaia = _gaia_rows_at_position(candidate)
    parameters = {"highEbv": HIGH_EBV, "sfdRecalibration": SFD_RECALIBRATION, "pointSourceRadiusArcsec": POINT_SOURCE_RADIUS_ARCSEC,
                  "starProbability": STAR_PROBABILITY, "parallaxSignificance": PARALLAX_SIGNIFICANCE,
                  "motionSignificance": MOTION_SIGNIFICANCE,
                  "rules": {"high-extinction": "recalibrated SFD E(B-V) >= highEbv",
                            "point-source": "a Gaia DR3 source within pointSourceRadiusArcsec with star probability >= starProbability, or a significant parallax or proper motion"}}
    inputs = {"raDeg": _round(ra, 9), "decDeg": _round(dec, 9), "gaiaAssertionIds": [str(r.get("assertion_id")) for _, r in gaia],
              "sfdMap": "SFD (1998) via the pinned reference data of Track 3c part 1"}
    if sfd_directory is None:
        return _run("extinction-and-point-source", "sfd-extinction-and-gaia-point-source", inputs=inputs, parameters=parameters,
                    reason="the pinned SFD map is not available in this export", claim=CLAIM_EXT)
    ebv_sfd = float(sfd_ebv(ra, dec, sfd_directory))
    ebv = ebv_sfd * SFD_RECALIBRATION
    point = None
    for sep, row in gaia:
        star, reasons, _ = _star_like(row)
        if star:
            point = {"gaiaSourceId": str(row.get("catalog_record_id")), "separationArcsec": _round(sep), "reasons": reasons}
            break
    result = {"ebvSfd": _round(ebv_sfd), "ebvRecalibrated": _round(ebv), "highExtinction": ebv >= HIGH_EBV,
              "pointSourceFlag": point is not None, "pointSource": point,
              "gaiaSourcesAtPosition": len(gaia)}
    warnings = ["Pan-STARRS point-source classifications are not retained by CTAS; the point-source flag uses Gaia DR3 only"]
    return _run("extinction-and-point-source", "sfd-extinction-and-gaia-point-source", inputs=inputs, parameters=parameters,
                result=result, warnings=warnings, claim=CLAIM_EXT, references=["1998ApJ...500..525S", "2011ApJ...737..103S", "2023A&A...674A...1G"])


def stellar_counterpart_run(candidate: dict[str, Any]) -> dict[str, Any] | None:
    gaia = _gaia_rows_at_position(candidate)
    if not gaia:
        return None
    parameters = {"parallaxSignificance": PARALLAX_SIGNIFICANCE, "radiusArcsec": POINT_SOURCE_RADIUS_ARCSEC,
                  "rule": "M_G = G + 5 log10(parallax / 100 mas) for the nearest Gaia DR3 source with parallax / error >= parallaxSignificance; no extinction correction"}
    inputs = {"gaiaAssertionIds": [str(r.get("assertion_id")) for _, r in gaia]}
    for sep, row in gaia:
        _, _, parts = _star_like(row)
        motion, flags = parts["motion"], parts["flags"]
        plx, plx_err = _float(motion.get("parallax_mas")), _float(motion.get("parallax_error_mas"))
        photometry = _json(row.get("photometry"))
        g = _float((photometry.get("G") or {}).get("magnitude"))
        bp_rp = _float(flags.get("bp_minus_rp_mag"))
        if plx is None or not plx_err or plx <= 0 or plx / plx_err < PARALLAX_SIGNIFICANCE or g is None or bp_rp is None:
            continue
        result = {"gaiaSourceId": str(row.get("catalog_record_id")), "separationArcsec": _round(sep),
                  "absoluteG": _round(g + 5 * math.log10(plx / 100.0)), "bpMinusRp": _round(bp_rp),
                  "apparentG": _round(g), "parallaxMas": _round(plx), "parallaxErrorMas": _round(plx_err),
                  "distancePc": _round(1000.0 / plx), "ruwe": _round(_float(flags.get("ruwe")))}
        return _run("stellar-counterpart", "gaia-dr3-colour-magnitude", inputs=inputs, parameters=parameters, result=result,
                    claim=CLAIM_STAR, references=["2023A&A...674A...1G"])
    return _run("stellar-counterpart", "gaia-dr3-colour-magnitude", inputs=inputs, parameters=parameters,
                reason="no Gaia DR3 source at the position has a significant parallax and G and BP-RP photometry", claim=CLAIM_STAR)


# ----------------------------------------------------------------------------- block
def blast_link(candidate: dict[str, Any]) -> dict[str, Any] | None:
    match = TNS_NAME.match(str(candidate.get("name") or "").strip())
    if not match:
        return None
    return {"label": "Blast host SED and properties (SCIMMA)", "url": BLAST_URL + match.group(1),
            "note": "External service; the page exists only if Blast has processed this transient."}


def build_host_association(candidate: dict[str, Any], sfd_directory: str | None = None, sfd_ebv=None) -> dict[str, Any] | None:
    """The block for one candidate, or None when no run has anything to report."""

    runs = [host_posterior_run(candidate)]
    for maker in (lambda: prior_activity_run(candidate),
                  lambda: extinction_point_source_run(candidate, sfd_directory, sfd_ebv) if sfd_ebv is not None else None,
                  lambda: stellar_counterpart_run(candidate)):
        run = maker()
        if run is not None:
            runs.append(run)
    # The extinction run alone does not make a block (every positioned event would change);
    # it joins a block that exists for another reason, or one whose flag is raised.
    def says_something(run: dict[str, Any]) -> bool:
        if run.get("status") != "complete":
            return False
        if run.get("analysisType") != "extinction-and-point-source":
            return True
        result = run.get("result") or {}
        return bool(result.get("highExtinction") or result.get("pointSourceFlag"))

    if not any(says_something(run) for run in runs):
        return None
    block: dict[str, Any] = {"schema": SCHEMA, "runs": runs}
    link = blast_link(candidate)
    if link:
        block["externalLinks"] = [link]
    return block


def probable_host_offset(block: dict[str, Any] | None) -> float | None:
    if not block:
        return None
    for run in block.get("runs") or []:
        if run.get("analysisType") == "host-posterior" and run.get("status") == "complete":
            return (run.get("result") or {}).get("probableHostOffsetArcsec")
    return None


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def host_problems(candidates: Iterable[dict[str, Any]]) -> list[str]:
    """Certificate gate: every run names its method version and inputs; complete runs carry a
    matching result checksum; posteriors are probabilities that sum to one; every ranked
    candidate traces to retained catalogue rows of the same dossier."""

    problems: list[str] = []
    for candidate in candidates:
        block = candidate.get(CANDIDATE_KEY)
        if block is None:
            continue
        name = candidate.get("name") or candidate.get("event_id")
        if not isinstance(block, dict) or block.get("schema") != SCHEMA or not isinstance(block.get("runs"), list):
            problems.append(f"{name}: {CANDIDATE_KEY} is not a {SCHEMA} block")
            continue
        retained = {str(row.get("assertion_id")) for row in ((candidate.get("follow_up") or {}).get("catalog_counterparts") or [])}
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
                if run.get("analysisType") == "host-posterior":
                    result = run.get("result") or {}
                    total = sum(float(c.get("posterior") or 0) for c in result.get("candidates") or []) + float(result.get("hostMissedPosterior") or 0)
                    if abs(total - 1.0) > 0.01:
                        problems.append(f"{label}: posteriors sum to {total:.4f}")
                    for ranked in result.get("candidates") or []:
                        if not ranked.get("assertionIds") or not set(ranked["assertionIds"]) <= retained:
                            problems.append(f"{label}: candidate {ranked.get('name')} does not trace to retained catalogue rows")
            elif run.get("status") == "not-computed":
                if not run.get("notComputedReason") or "result" in run:
                    problems.append(f"{label}: a not-computed run must give a reason and no result")
            else:
                problems.append(f"{label}: unknown status")
    return problems
