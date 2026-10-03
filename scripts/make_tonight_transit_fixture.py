#!/usr/bin/env python3
"""Reference values from astropy for the Tonight's sky transit predictor.

Writes tests/fixtures/tonight-transits.json, read by scripts/test_tonight_planner.mjs:

  barycentric  BJD_TDB of UTC instants for targets around the sky through 2026 (astropy
               Time.light_travel_time, kind='barycentric', with its built-in ephemeris).
  transits     synthetic ephemerides placed so that transits fall in, across the edge of, or
               outside the dark part of the night of 2026-10-03 at Laramie and Cerro Pachón;
               for each, the UTC of ingress, mid-transit and egress (the BJD_TDB instants
               inverted with astropy), the target's altitude at each (no refraction), the
               Moon's separation at mid-transit, whether the whole transit is dark (Sun below
               -12 deg) with the target above the altitude limit, and the propagated 1-sigma
               mid-time uncertainty.

Re-run after changing a case:  python3 scripts/make_tonight_transit_fixture.py
"""
from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import numpy as np
from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body, get_sun
from astropy.time import Time
from astropy.utils import iers

iers.conf.auto_download = False
warnings.filterwarnings("ignore")

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tonight-transits.json"
SITES = {
    "laramie": {"lat": 41.3114, "lon": -105.5911, "tz": "America/Denver", "date": "2026-10-03"},
    "pachon": {"lat": -30.2446, "lon": -70.7494, "tz": "America/Santiago", "date": "2026-10-03"},
}
TWILIGHT, ALT_LIMIT = -12.0, 30.0


def location(site):
    return EarthLocation(lat=site["lat"] * u.deg, lon=site["lon"] * u.deg, height=0 * u.m)


def bjd_of(utc: Time, coord: SkyCoord, loc: EarthLocation) -> float:
    t = Time(utc, scale="utc", location=loc)
    return float((t.tdb + t.light_travel_time(coord, kind="barycentric")).jd)


def utc_of(bjd: float, coord: SkyCoord, loc: EarthLocation) -> Time:
    """Invert BJD_TDB -> UTC with astropy (fixed point; converges to microseconds)."""
    guess = Time(bjd, format="jd", scale="tdb").utc
    for _ in range(4):
        guess = guess + (bjd - bjd_of(guess, coord, loc)) * u.day
    return guess


def altitude(t: Time, coord: SkyCoord, loc: EarthLocation) -> float:
    return float(coord.transform_to(AltAz(obstime=t, location=loc)).alt.deg)


def iso(t: Time) -> str:
    return t.utc.isot[:23] + "Z"


def barycentric_cases():
    loc = location(SITES["laramie"])
    cases = []
    for when in ("2026-01-15T03:00:00", "2026-03-20T12:00:00", "2026-06-21T06:30:00", "2026-09-22T18:00:00", "2026-10-04T04:00:00", "2026-12-21T00:00:00"):
        for ra, dec in ((0.0, 0.0), (90.0, 23.44), (180.0, -30.0), (270.0, -66.56), (300.0, 40.0), (45.0, 89.0), (120.0, -80.0)):
            coord = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
            t = Time(when, scale="utc")
            cases.append({"utc": when + "Z", "raDeg": ra, "decDeg": dec, "bjdTdb": bjd_of(t, coord, loc)})
    return cases


def transit_case(site_key, name, ra, dec, mid_utc, period, duration_h, epochs_back, sigma_t0, sigma_p, depth_ppt=5.0):
    site = SITES[site_key]
    loc, coord = location(site), SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
    mid_bjd = bjd_of(Time(mid_utc, scale="utc"), coord, loc)
    t0 = round(mid_bjd - epochs_back * period, 6)
    mid_bjd = t0 + epochs_back * period  # the exact catalog-predicted instant
    half = duration_h / 48.0
    ingress, mid, egress = (utc_of(mid_bjd + d, coord, loc) for d in (-half, 0.0, half))
    # Sample the transit every two minutes for the "whole transit observable" flag.
    n = max(2, int(round(duration_h * 30)) + 1)
    times = ingress + np.linspace(0.0, 1.0, n) * (egress - ingress)
    frame = AltAz(obstime=times, location=loc)
    target_alt = coord.transform_to(frame).alt.deg
    sun_alt = get_sun(times).transform_to(frame).alt.deg
    good = (target_alt >= ALT_LIMIT) & (sun_alt < TWILIGHT)
    moon = get_body("moon", mid, loc)
    moon_sep = float(moon.separation(SkyCoord(ra * u.deg, dec * u.deg, frame="icrs").transform_to(moon.frame)).deg)
    return {
        "site": site_key,
        "ephemeris": {"objectId": f"fixture-{name.lower().replace(' ', '-')}", "name": name, "host": name[:-2], "raDeg": ra, "decDeg": dec,
                      "status": "CONFIRMED", "periodDays": period, "periodErrDays": sigma_p, "t0Bjd": t0, "t0ErrDays": sigma_t0,
                      "durationHours": duration_h, "depthPpt": depth_ppt, "sourceId": "nasa-toi"},
        "epoch": epochs_back,
        "expected": {
            "ingressUtc": iso(ingress), "midUtc": iso(mid), "egressUtc": iso(egress),
            "altIngress": altitude(ingress, coord, loc), "altMid": altitude(mid, coord, loc), "altEgress": altitude(egress, coord, loc),
            "moonSepDeg": moon_sep, "sigmaMinutes": math.hypot(sigma_t0, epochs_back * sigma_p) * 1440.0,
            "observableFraction": float(good.mean()), "full": bool(good.all()), "listed": bool(good.any()),
            "barycentricSeconds": (mid_bjd - float(Time(mid, scale="utc").tdb.jd)) * 86400.0,
        },
    }


def main():
    transits = [
        # Whole transit in the dark, target high: listed, full.
        transit_case("laramie", "Fixture Full b", 300.0, 40.0, "2026-10-04T04:00:00", 2.5, 3.0, 250, 0.0004, 2e-6, 12.0),
        # Ingress in twilight (dark from ~01:40 UTC): listed, partial.
        transit_case("laramie", "Fixture Dusk b", 280.0, 20.0, "2026-10-04T01:35:00", 1.7, 2.5, 400, 0.0002, 1e-6),
        # Target sinks below 30 deg during the transit: listed, partial.
        transit_case("laramie", "Fixture Setting b", 281.0, 0.0, "2026-10-04T03:30:00", 3.3, 2.5, 120, 0.001, 5e-6),
        # Long period with a loose period: listed, full, sigma of tens of minutes.
        transit_case("pachon", "Fixture South b", 10.0, -45.0, "2026-10-04T05:30:00", 45.0, 4.0, 40, 0.002, 0.0006, 1.5),
        # Mid-transit at local noon: not listed.
        transit_case("laramie", "Fixture Daytime b", 300.0, 40.0, "2026-10-03T19:00:00", 4.1, 2.0, 100, 0.0005, 3e-6),
        # Never rises at Laramie: not listed.
        transit_case("laramie", "Fixture Polar b", 120.0, -80.0, "2026-10-04T07:00:00", 2.2, 2.0, 100, 0.0005, 3e-6),
    ]
    payload = {
        "generator": "scripts/make_tonight_transit_fixture.py",
        "astropy": __import__("astropy").__version__,
        "twilightDeg": TWILIGHT, "altLimitDeg": ALT_LIMIT, "sites": SITES,
        "barycentric": barycentric_cases(), "transits": transits,
    }
    OUT.write_text(json.dumps(payload, indent=1) + "\n")
    for case in transits:
        e = case["expected"]
        print(f"{case['ephemeris']['name']:20s} {e['midUtc']} alt {e['altIngress']:.1f}/{e['altMid']:.1f}/{e['altEgress']:.1f} "
              f"frac {e['observableFraction']:.2f} full {e['full']} listed {e['listed']} sigma {e['sigmaMinutes']:.1f} min moon {e['moonSepDeg']:.1f}")


if __name__ == "__main__":
    main()
