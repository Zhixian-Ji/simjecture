"""Permanently non-evidentiary interface check using FEOS's upstream aluminium deck."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path


def main():
    checks = dict(executable_present=False, data_present=False, process_completed=False,
                  return_code_zero=False, result_finite=False, component_sums=False)
    payload = dict(package="FEOS", version="16.7",
                   scientific_status="permanently_non_evidentiary", checks=checks)
    try:
        query = Path(os.environ["FEOS_QUERY"])
        data = Path(os.environ["FEOS_DATA"])
        checks["executable_present"] = query.is_file() and os.access(query, os.X_OK)
        names = ("FEOS_Material-DB.dat", "FEOS_TF-Table_1197.dat")
        checks["data_present"] = all((data / name).is_file() for name in names)
        if not checks["executable_present"] or not checks["data_present"]:
            raise ValueError("Configured FEOS runtime or data is missing")
        for name in names:
            shutil.copy2(data / name, name)
        run = subprocess.run(
            [str(query), "3717", "2.7", "10", "feos_query.json"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        payload.update(return_code=run.returncode, stdout=run.stdout, stderr=run.stderr)
        checks["process_completed"] = True
        checks["return_code_zero"] = run.returncode == 0
        if run.returncode:
            raise ValueError("FEOS query failed")
        result = json.loads(Path("feos_query.json").read_text())
        payload["result"] = result
        fields = ("pressure", "energy", "electron_pressure", "electron_energy",
                  "ion_pressure", "ion_energy", "zbar")
        checks["result_finite"] = all(math.isfinite(result[key]) for key in fields)
        checks["component_sums"] = all(
            math.isclose(result[key], result["electron_" + key] + result["ion_" + key],
                         rel_tol=1e-10, abs_tol=1e-5)
            for key in ("pressure", "energy")
        )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        payload["error"] = str(error)
    checks["completed"] = all(checks.values())
    Path("feos_capability_smoke.json").write_text(json.dumps(payload, indent=2) + "\n")
    return 0 if checks["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
