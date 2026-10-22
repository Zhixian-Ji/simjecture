"""IONMIX4 serialization. Values here use the on-disk IONMIX units.

See FLASH 4.8 eos_tabReadIonmix4Tables.F90 and the official IONMIX format
documentation. Each block starts a new record; T varies fastest.
"""

import math
from pathlib import Path

import numpy as np

FIELDS = (
    "zbar",
    "dzdt",
    "pion",
    "pele",
    "dpidt",
    "dpedt",
    "eion",
    "eele",
    "cvion",
    "cvele",
    "deidn",
    "deedn",
)


def format_e12(value):
    """Fortran-compatible E12.6 with six significant mantissa digits."""
    if not math.isfinite(value):
        raise ValueError("IONMIX cannot contain NaN or infinity")
    if value == 0:
        return "0.000000E+00"
    mantissa, exponent = f"{abs(value):.5E}".split("E")
    digits = mantissa.replace(".", "")
    exp = int(exponent) + 1
    if not -99 <= exp <= 99:
        raise ValueError("Exponent outside two-digit IONMIX range")
    text = ("-." if value < 0 else "0.") + digits + f"E{exp:+03d}"
    if len(text) != 12:
        raise ValueError(f"Invalid IONMIX field: {text}")
    return text


def write_ionmix4(path, temperatures_ev, number_densities_cm3, tables, z):
    t, n = np.asarray(temperatures_ev), np.asarray(number_densities_cm3)
    if len(t) < 2 or len(n) < 2 or np.any(t <= 0) or np.any(n <= 0):
        raise ValueError("Positive grids with at least two points are required")
    if np.any(np.diff(t) <= 0) or np.any(np.diff(n) <= 0):
        raise ValueError("IONMIX grids must be strictly increasing")
    for key in FIELDS:
        if np.shape(tables[key]) != (len(n), len(t)):
            raise ValueError(f"Incorrect shape for {key}")
    with Path(path).open("w") as f:
        f.write(f"{len(t):10d}{len(n):10d}\n")
        f.write(f" atomic #s of gases: {z:10d}\n")
        f.write(f" relative fractions: {1.0:10.2E}\n")
        # EOS-only: zero radiation groups, not invented zero-opacity material.
        f.write(f"{0:12d}\n")

        def block(values):
            a = np.asarray(values).ravel(order="C")
            for start in range(0, len(a), 4):
                f.write("".join(format_e12(float(v)) for v in a[start : start + 4]) + "\n")

        block(t)
        block(n)
        for key in FIELDS:
            block(tables[key])
        # ngroups+1 boundary entries; no opacity arrays when ngroups=0.
        block([0.0])


def read_ionmix4(path):
    """Strict local reader for file-level tests; FLASH also validates independently."""
    with Path(path).open() as f:
        header = f.readline()
        nt, nd = int(header[:10]), int(header[10:20])
        f.readline()
        f.readline()
        ng = int(f.readline())
        if ng != 0:
            raise ValueError("This reader is for our EOS-only exports")

        def block(count):
            out = []
            while len(out) < count:
                line = f.readline().rstrip("\n")
                if not line or len(line) % 12 or len(line) > 48:
                    raise ValueError("Truncated or malformed IONMIX block")
                out.extend(float(line[i : i + 12]) for i in range(0, len(line), 12))
            if len(out) != count:
                raise ValueError("IONMIX block crosses a record boundary")
            return np.array(out)

        t, n = block(nt), block(nd)
        tables = {key: block(nt * nd).reshape(nd, nt) for key in FIELDS}
        bounds = block(1)
        if f.read().strip():
            raise ValueError("Unexpected trailing EOS-only data")
    return {"temps_ev": t, "number_density_cm3": n, "ngroups": ng, "bounds_ev": bounds, **tables}
