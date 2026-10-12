#!/usr/bin/env python3
"""Write the FITS spectrum fixtures in tests/fixtures/spectra/ (standard library only).

Each file imitates a layout TNS or WISeREP uploads arrive in, so that
ctas/spectrum-fits.js is tested against bytes written by something other than
itself. Re-run after changing a layout here; the files are small and committed.

    python3 scripts/make_spectrum_fits_fixtures.py
"""
from __future__ import annotations

import gzip
import math
import struct
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "spectra"
BLOCK = 2880


def card(key: str, value, comment: str = "") -> str:
    if isinstance(value, bool):
        text = ("T" if value else "F").rjust(20)
    elif isinstance(value, int):
        text = str(value).rjust(20)
    elif isinstance(value, float):
        text = (f"{value:.12G}" if ("E" in f"{value:.12G}" or "." in f"{value:.12G}") else f"{value:.12G}.").rjust(20)
    else:
        text = "'" + str(value).replace("'", "''").ljust(8) + "'"
    line = f"{key:<8}= {text}"
    if comment:
        line += f" / {comment}"
    return line[:80].ljust(80)


def header(cards: list[str]) -> bytes:
    text = "".join(cards) + "END".ljust(80)
    return text.ljust(math.ceil(len(text) / BLOCK) * BLOCK).encode("ascii")


def padded(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % BLOCK)


def image_cards(bitpix: int, shape: list[int], primary: bool = True, extra: list[str] | None = None) -> list[str]:
    cards = [card("SIMPLE", True, "conforms to FITS standard") if primary else card("XTENSION", "IMAGE"),
             card("BITPIX", bitpix), card("NAXIS", len(shape))]
    cards += [card(f"NAXIS{i + 1}", n) for i, n in enumerate(shape)]
    if not primary:
        cards += [card("PCOUNT", 0), card("GCOUNT", 1)]
    elif len(shape):
        cards.append(card("EXTEND", True))
    return cards + (extra or [])


def floats(values, fmt=">f") -> bytes:
    return b"".join(struct.pack(fmt, v) for v in values)


def spectrum(n: int, start: float, step: float):
    """A synthetic supernova-like continuum with an absorption trough and emission bump."""
    waves = [start + step * i for i in range(n)]
    flux = []
    for w in waves:
        value = 2.0e-16 * math.exp(-((w - 5000.0) / 2500.0) ** 2)
        value *= 1 - 0.45 * math.exp(-((w - 6150.0) / 80.0) ** 2)
        value *= 1 + 0.35 * math.exp(-((w - 6563.0) / 40.0) ** 2)
        flux.append(value)
    return waves, flux


def write(name: str, parts: list[bytes]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_bytes(b"".join(parts))


def main() -> None:
    meta = [card("OBJECT", "SN 2026fit"), card("DATE-OBS", "2026-09-12T10:29:45"), card("TELESCOP", "P60"),
            card("INSTRUME", "SEDM"), card("EXPTIME", 2700.0), card("BUNIT", "erg/cm2/s/A")]

    # 1. Primary 1-D image, float32, linear air wavelengths in Angstrom, two NaN pixels.
    waves, flux = spectrum(400, 3600.0, 14.0)
    flux[10] = float("nan")
    flux[11] = float("nan")
    write("linear-1d.fits", [header(image_cards(-32, [400], extra=meta + [
        card("CTYPE1", "AWAV"), card("CUNIT1", "Angstrom"), card("CRVAL1", 3600.0), card("CDELT1", 14.0), card("CRPIX1", 1.0)])),
        padded(floats(flux))])
    (OUT / "linear-1d.fits.gz").write_bytes(gzip.compress((OUT / "linear-1d.fits").read_bytes(), mtime=0))

    # 2. IRAF-style log10 dispersion in a scaled int16 image with a BLANK pixel.
    n = 300
    log_waves = [3.55 + 1.0e-4 * i for i in range(n)]
    _, flux = spectrum(n, 10 ** 3.55, 1.0)
    flux = [2.0e-16 * (1 + 0.3 * math.sin(i / 20.0)) for i in range(n)]
    scale, zero = 1.0e-19, 2.0e-16
    ints = [int(round((f - zero) / scale)) for f in flux]
    ints[5] = -32768  # BLANK
    write("log-wave-int16.fits", [header(image_cards(16, [n], extra=meta + [
        card("BZERO", zero), card("BSCALE", scale), card("BLANK", -32768), card("CTYPE1", "LINEAR"), card("DC-FLAG", 1),
        card("CRVAL1", 3.55), card("CDELT1", 1.0e-4), card("CRPIX1", 1.0),
        card("WAT0_001", "system=equispec"), card("WAT1_001", "wtype=linear label=Wavelength units=angstroms")])),
        padded(floats(ints, ">h"))])

    # 3. IRAF multispec: 2 apertures x 4 bands (flux, no-sky, sky, sigma); spec1 linear.
    n1, n2, n3 = 320, 2, 4
    waves, flux = spectrum(n1, 4000.0, 3.0)
    bands = []
    for band in range(n3):
        for ap in range(n2):
            if band == 0:
                bands += [f * (1 if ap == 0 else 0.5) for f in flux]
            elif band == 3:
                bands += [f * 0.05 for f in flux]
            else:
                bands += [f * 0.1 for f in flux]
    wat2 = ('wtype=multispec spec1 = "1 1 0 4000. 3. 320 0. 10. 20." spec2 = "2 2 0 4000. 3. 320 0. 30. 40."')
    wat_cards = [card(f"WAT2_{i + 1:03d}", wat2[i * 68:(i + 1) * 68]) for i in range(math.ceil(len(wat2) / 68))]
    write("multispec-3d.fits", [header(image_cards(-32, [n1, n2, n3], extra=meta + [
        card("CTYPE1", "MULTISPE"), card("CTYPE2", "MULTISPE"), card("CTYPE3", "LINEAR"), card("CDELT1", 1.0), card("CDELT2", 1.0), card("CRVAL1", 1.0),
        card("BANDID1", "spectrum - background fit, weights variance, clean no"), card("BANDID2", "raw - background fit, weights none, clean no"),
        card("BANDID3", "background - background fit"), card("BANDID4", "sigma - background fit, weights variance, clean no"),
        card("WAT0_001", "system=multispec"), card("WAT1_001", "wtype=multispec label=Wavelength units=angstroms")] + wat_cards)),
        padded(floats(bands))])

    # 4. SDSS-like: empty primary, an empty IMAGE extension, then a BINTABLE with one row per
    #    pixel: flux (E), loglam (D), ivar (E), and_mask (J).
    n = 500
    loglam = [3.58 + 1.0e-4 * i for i in range(n)]
    _, flux = spectrum(n, 10 ** 3.58, 1.0)
    flux = [f * 1e17 for f in flux]  # 1e-17 erg/s/cm2/A units
    rows = b"".join(struct.pack(">fdfi", flux[i], loglam[i], 0.0 if i == 7 else 25.0, 0) for i in range(n))
    table = [card("XTENSION", "BINTABLE"), card("BITPIX", 8), card("NAXIS", 2), card("NAXIS1", 20), card("NAXIS2", n),
             card("PCOUNT", 0), card("GCOUNT", 1), card("TFIELDS", 4), card("EXTNAME", "COADD"),
             card("TTYPE1", "flux"), card("TFORM1", "E"), card("TUNIT1", "1E-17 erg/s/cm^2/Ang"),
             card("TTYPE2", "loglam"), card("TFORM2", "D"), card("TUNIT2", "log10(Ang)"),
             card("TTYPE3", "ivar"), card("TFORM3", "E"), card("TTYPE4", "and_mask"), card("TFORM4", "J")]
    write("bintable-rows-sdss.fits", [header(image_cards(8, [], extra=meta[:1] + [card("TELESCOP", "SDSS 2.5-M"), card("VACUUM", True)])),
        header(image_cards(8, [], primary=False)), header(table), padded(rows)])

    # 5. ESO-style one-row table of vector cells: WAVE in nm (D), FLUX (E), ERR (E), QUAL (J).
    n = 450
    waves, flux = spectrum(n, 3700.0, 12.0)
    row = floats([w / 10.0 for w in waves], ">d") + floats(flux) + floats([f * 0.04 for f in flux]) + floats([0] * n, ">i")
    table = [card("XTENSION", "BINTABLE"), card("BITPIX", 8), card("NAXIS", 2), card("NAXIS1", len(row)), card("NAXIS2", 1),
             card("PCOUNT", 0), card("GCOUNT", 1), card("TFIELDS", 4), card("EXTNAME", "SPECTRUM"),
             card("TTYPE1", "WAVE"), card("TFORM1", f"{n}D"), card("TUNIT1", "nm"),
             card("TTYPE2", "FLUX"), card("TFORM2", f"{n}E"), card("TUNIT2", "erg.cm**(-2).s**(-1).angstrom**(-1)"),
             card("TTYPE3", "ERR"), card("TFORM3", f"{n}E"), card("TTYPE4", "QUAL"), card("TFORM4", f"{n}J"),
             card("INSTRUME", "FORS2"), card("EXPTIME", 900.0), card("BUNIT", "erg.cm**(-2).s**(-1).angstrom**(-1)")]
    write("bintable-vector-eso.fits", [header(image_cards(8, [], extra=[card("OBJECT", "AT 2026eso"), card("TELESCOP", "ESO-VLT-U1"), card("DATE-OBS", "2026-10-01T03:12:00.000")])),
        header(table), padded(row)])

    # 6. Rows of wavelength, flux, error with no WCS at all (numpy.savetxt-style arrays saved as FITS).
    n = 260
    waves, flux = spectrum(n, 3800.0, 20.0)
    write("pairs-2d.fits", [header(image_cards(-64, [n, 3], extra=[card("OBJECT", "AT 2026pair")])),
        padded(floats(waves, ">d") + floats(flux, ">d") + floats([f * 0.07 for f in flux], ">d"))])

    # 7. Rejected: a 2-D image with no wavelength axis, and a spectrum cut short.
    write("image-2d-noaxis.fits", [header(image_cards(16, [64, 64], extra=[card("OBJECT", "a frame")])), padded(b"\0" * 64 * 64 * 2)])
    waves, flux = spectrum(1000, 3600.0, 6.0)
    whole = header(image_cards(-32, [1000], extra=[card("CRVAL1", 3600.0), card("CDELT1", 6.0)])) + padded(floats(flux))
    (OUT / "truncated.fits").write_bytes(whole[: len(whole) - 2880])
    print("wrote FITS fixtures to", OUT)


if __name__ == "__main__":
    main()
