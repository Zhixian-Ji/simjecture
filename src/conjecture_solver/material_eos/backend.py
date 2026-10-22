"""Explicit FEOS backends. All Python-facing thermodynamic quantities are SI."""

from __future__ import annotations

import csv
import ctypes
import hashlib
import json
import math
import os
import subprocess
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.constants import Boltzmann, elementary_charge

K_PER_EV = elementary_charge / Boltzmann


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ThomasFermi:
    """Raw TF (P,E,F,S) and Zbar, with no material/energy-reference correction."""

    def __init__(self, atomic_weight, z, library=None, data=None):
        library = Path(library or os.environ["FEOS_TF_LIBRARY"]).resolve(strict=True)
        data = Path(data or os.environ["FEOS_DATA"]).resolve(strict=True)
        table = data / "FEOS_TF-Table_1197.dat"
        self.identity = {
            "library_sha256": digest(library),
            "TF_table_sha256": digest(table),
            "model": "FEOS 16.7 raw Thomas-Fermi",
            "atomic_weight": atomic_weight,
            "atomic_number": z,
        }
        self.lib = ctypes.CDLL(str(library))
        self.lib.material_tf_create.argtypes = [ctypes.c_char_p, ctypes.c_double, ctypes.c_double]
        self.lib.material_tf_create.restype = ctypes.c_void_p
        self.lib.material_tf_evaluate.argtypes = [
            ctypes.c_void_p,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.POINTER(ctypes.c_double),
        ]
        self.lib.material_tf_evaluate.restype = ctypes.c_int
        self.lib.material_tf_destroy.argtypes = [ctypes.c_void_p]
        self.lib.material_tf_destroy.restype = None
        self.handle = self.lib.material_tf_create(str(table).encode(), atomic_weight, z)
        if not self.handle:
            raise ValueError("Cannot initialize the FEOS raw-TF backend")
        self.lock = threading.RLock()
        self.z = z
        # Per-instance caches do not keep unrelated material handles alive.
        self._state = lru_cache(maxsize=32768)(self._uncached)

    def _uncached(self, rho, temperature):
        if not self.handle:
            raise ValueError("FEOS backend has been closed")
        out = (ctypes.c_double * 5)()
        with self.lock:
            code = self.lib.material_tf_evaluate(self.handle, rho, temperature, out)
        if code or not all(math.isfinite(v) for v in out):
            raise ValueError(f"TF evaluation failed at rho={rho}, T={temperature}, code={code}")
        if temperature > 0 and not 0 <= out[4] <= self.z:
            raise ValueError("TF mean ionization outside the atomic-number bounds")
        return tuple(out)

    def evaluate(self, rho, temperature):
        return self._state(float(rho), float(temperature))[:4]

    def ionization(self, rho, temperature):
        if temperature <= 0:
            raise ValueError("Mean-ionization query requires positive temperature")
        return self._state(float(rho), float(temperature))[4]

    def bulk_modulus(self, rho, temperature, h=0.001):
        values = [self.evaluate(rho * (1 + k * h), temperature)[0] for k in (-2, -1, 1, 2)]
        return float(np.dot([1, -8, 8, -1], values) / (12 * h))

    def close(self):
        if getattr(self, "handle", None):
            self.lib.material_tf_destroy(self.handle)
            self.handle = None
            self._state.cache_clear()

    def __del__(self):
        self.close()


class NativeFEOS:
    """PR #35's native QEOS adapter; the corrected electron term includes bonding."""

    def __init__(self, database, material_id, workspace, query=None, data=None):
        self.query = Path(query or os.environ["FEOS_QUERY"]).resolve(strict=True)
        self.data = Path(data or os.environ["FEOS_DATA"]).resolve(strict=True)
        self.database = Path(database).resolve(strict=True)
        if type(material_id) is not int or not 1000 <= material_id <= 9999:
            raise ValueError("FEOS material ID must be an integer from 1000 through 9999")
        self.material_id = material_id
        self.workspace = Path(workspace)
        self.identity = {
            "query_sha256": digest(self.query),
            "database_sha256": digest(self.database),
            "TF_table_sha256": digest(self.data / "FEOS_TF-Table_1197.dat"),
            "material_id": material_id,
            "model": "FEOS 16.7 native QEOS",
        }

    def _run(self, args, folder):
        process = subprocess.run(
            [str(self.query), *args],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if process.returncode:
            raise ValueError(f"FEOS query failed ({process.returncode}): {process.stderr[-2000:]}")

    def _input_files(self, folder):
        import shutil

        shutil.copy2(self.database, folder / "FEOS_Material-DB.dat")
        shutil.copy2(self.data / "FEOS_TF-Table_1197.dat", folder / "FEOS_TF-Table_1197.dat")

    def describe(self, rho, temperature):
        with tempfile.TemporaryDirectory(dir=self.workspace) as directory:
            folder = Path(directory)
            self._input_files(folder)
            self._run(
                [str(self.material_id), str(rho / 1000), str(temperature / K_PER_EV), "state.json"],
                folder,
            )
            result = json.loads((folder / "state.json").read_text())
        for key in (
            "atomic_mass",
            "atomic_number",
            "reference_density",
            "reference_temperature",
            "reference_bulk_modulus",
            "electron_energy_offset",
            "ion_energy_offset",
        ):
            if key not in result or not math.isfinite(result[key]):
                raise ValueError(f"Missing or nonfinite FEOS metadata: {key}")
        self.metadata = result
        return result

    def batch(self, pairs):
        pairs = list(pairs)
        if any(
            not (math.isfinite(r) and math.isfinite(t) and r > 0 and t / K_PER_EV >= 1e-4)
            for r, t in pairs
        ):
            raise ValueError("Native FEOS states must be finite and exceed its 1e-4 eV floor")
        with tempfile.TemporaryDirectory(dir=self.workspace) as directory:
            folder = Path(directory)
            self._input_files(folder)
            with (folder / "states.csv").open("w") as f:
                writer = csv.writer(f, lineterminator="\n")
                writer.writerow(["rho_g_cm3", "temperature_eV"])
                writer.writerows((r / 1000, t / K_PER_EV) for r, t in pairs)
            self._run(["--batch", str(self.material_id), "states.csv", "output.csv"], folder)
            with (folder / "output.csv").open() as f:
                rows = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]
        if len(rows) != len(pairs):
            raise ValueError("FEOS batch output row count mismatch")
        for row, (rho, t) in zip(rows, pairs, strict=True):
            if not all(math.isfinite(v) for v in row.values()):
                raise ValueError("Nonfinite FEOS batch output")
            if not (
                math.isclose(row["rho_g_cm3"], rho / 1000, rel_tol=1e-12)
                and math.isclose(row["temperature_eV"], t / K_PER_EV, rel_tol=1e-12)
            ):
                raise ValueError("FEOS batch output state/order mismatch")
            for key in ("pressure", "energy", "entropy", "free_energy"):
                if not math.isclose(
                    row[key], row["electron_" + key] + row["ion_" + key], rel_tol=1e-9, abs_tol=1e-3
                ):
                    raise ValueError(f"FEOS component sum failed for {key}")
            if not 0 <= row["zbar"] <= self.metadata["atomic_number"]:
                raise ValueError("FEOS mean ionization outside atomic-number bounds")
            for prefix in ("", "electron_", "ion_", "tf_"):
                row[prefix + "pressure"] *= 0.1
                row[prefix + "energy"] *= 1e-4
                row[prefix + "free_energy"] *= 1e-4
                row[prefix + "entropy"] *= 1e-4 / K_PER_EV
        return rows
