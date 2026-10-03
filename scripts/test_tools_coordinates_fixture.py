#!/usr/bin/env python3
"""Reference vectors for the coordinate converter (tools/coordinates/), from astropy.

Writes scripts/test_tools_coordinates_fixture.json, which scripts/test_tools_coordinates.mjs
compares against tools/lib/astrometry.js. Run it again only to regenerate the fixture (the
cases are seeded, so the file is reproducible for a given astropy version):

    python3 scripts/test_tools_coordinates_fixture.py          # rewrite the fixture
    python3 scripts/test_tools_coordinates_fixture.py --check  # compare with the committed file

No network is used: IERS downloads are switched off and UT1 is taken equal to UTC
(delta_ut1_utc = 0), the same assumption the browser code makes. Polar motion falls back
to astropy's long-term mean outside the bundled tables. Altitudes have no refraction
(pressure = 0).
"""
from __future__ import annotations

import json
import random
import sys
import warnings
from pathlib import Path

import numpy as np
import astropy
import astropy.units as u
from astropy.coordinates import (AltAz, BarycentricMeanEcliptic, EarthLocation, FK5, Galactic, SkyCoord, TETE,
                                 get_constellation)
from astropy.time import Time
from astropy.utils import iers

OUT = Path(__file__).with_name("test_tools_coordinates_fixture.json")
SITES = [  # (name, lat, lon): the Tonight's sky presets plus a few odd ones
    ("wiro", 41.0979, -105.9767), ("rbo", 41.1764, -105.5739), ("kpno", 31.9583, -111.5967),
    ("mko", 19.8207, -155.4681), ("lco", -29.0146, -70.6926), ("sso", -31.2733, 149.0617),
    ("lapalma", 28.7606, -17.8816), ("svalbard", 78.2232, 15.6267), ("equator", 0.0, 0.0),
    ("south-pole", -89.99, 139.27),
]


def build() -> dict:
    iers.conf.auto_download = False
    iers.conf.iers_degraded_accuracy = "ignore"
    warnings.simplefilter("ignore")
    rng = random.Random(20261003)
    cases = []
    for i in range(32):
        ra = rng.uniform(0, 360)
        dec = float(np.degrees(np.arcsin(rng.uniform(-1, 1))))
        if i == 0:
            ra, dec = 10.684708, 41.26875  # M31
        unix = rng.randrange(int(Time("1995-01-01").unix), int(Time("2035-12-31").unix))
        t = Time(unix, format="unix", scale="utc")
        t.format = "isot"
        t.delta_ut1_utc = 0.0
        name, lat, lon = SITES[i % len(SITES)]
        c = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
        gal = c.transform_to(Galactic())
        ecl = c.transform_to(BarycentricMeanEcliptic(equinox="J2000"))
        loc = EarthLocation.from_geodetic(lon * u.deg, lat * u.deg, 0 * u.m)
        altaz = c.transform_to(AltAz(obstime=t, location=loc, pressure=0 * u.hPa))
        tete = c.transform_to(TETE(obstime=t))
        # Constellation, and whether a 0.02 deg nudge changes it (then the test does not insist).
        const = str(get_constellation(c, short_name=True))
        nudged = {str(get_constellation(SkyCoord(ra=(ra + dra / max(np.cos(np.radians(dec)), 0.05)) * u.deg,
                                                 dec=np.clip(dec + ddec, -90, 90) * u.deg), short_name=True))
                  for dra in (-0.02, 0, 0.02) for ddec in (-0.02, 0, 0.02)}
        equinox = round(rng.uniform(1950, 2050), 2)
        fk5 = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame=FK5(equinox=f"J{equinox}")).transform_to(FK5(equinox="J2000"))
        cases.append({
            "ra": ra, "dec": dec, "unix_ms": unix * 1000, "utc": t.isot, "site": name, "lat": lat, "lon": lon,
            "jd": float(t.jd1 + t.jd2), "mjd": float(t.mjd),
            "gmst_h": float(t.sidereal_time("mean", longitude=0 * u.deg).hour),
            "gast_h": float(t.sidereal_time("apparent", longitude=0 * u.deg).hour),
            "lmst_h": float(t.sidereal_time("mean", longitude=lon * u.deg).hour),
            "last_h": float(t.sidereal_time("apparent", longitude=lon * u.deg).hour),
            "l": float(gal.l.deg), "b": float(gal.b.deg),
            "lambda": float(ecl.lon.deg), "beta": float(ecl.lat.deg),
            "alt": float(altaz.alt.deg), "az": float(altaz.az.deg),
            "tete_ra": float(tete.ra.deg), "tete_dec": float(tete.dec.deg),
            "constellation": const, "near_boundary": len(nudged) > 1,
            "equinox": equinox, "j2000_ra": float(fk5.ra.deg), "j2000_dec": float(fk5.dec.deg),
        })
    return {"generator": "scripts/test_tools_coordinates_fixture.py", "astropy": astropy.__version__,
            "notes": "UT1=UTC, no refraction, ecliptic = BarycentricMeanEcliptic(J2000), apparent = TETE",
            "cases": cases}


def main() -> int:
    data = build()
    text = json.dumps(data, indent=1) + "\n"
    if "--check" in sys.argv:
        old = json.loads(OUT.read_text())
        worst = max(abs(a[k] - b[k]) for a, b in zip(old["cases"], data["cases"])
                    for k in a if isinstance(a[k], float))
        print(f"largest difference from the committed fixture: {worst:.3g}")
        return 0 if worst < 1e-6 else 1
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(data['cases'])} cases, astropy {astropy.__version__})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
