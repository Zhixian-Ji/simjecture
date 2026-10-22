"""Permanently non-evidentiary builder/export interface check with an upstream deck."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    checks = dict(
        executable_present=False,
        process_completed=False,
        return_code_zero=False,
        table_written=False,
        serialization_checked=False,
        profile_recorded=False,
    )
    payload = dict(
        package="material-eos",
        version="0.1.0",
        scientific_status="permanently_non_evidentiary",
        checks=checks,
    )
    try:
        builder = Path(os.environ["MATERIAL_EOS_BUILD"])
        data = Path(os.environ["FEOS_DATA"])
        checks["executable_present"] = builder.is_file()
        shutil.copy2(data / "FEOS_Material-DB.dat", "FEOS_Material-DB.dat")
        Path("grid.json").write_text(
            json.dumps({"density_g_cm3": [2.7, 3.0], "temperature_K": [100000.0, 200000.0]})
        )
        process = subprocess.run(
            [
                sys.executable,
                str(builder),
                "build",
                "--profile",
                "native-feos",
                "--grid",
                "grid.json",
                "--database",
                "FEOS_Material-DB.dat",
                "--material-id",
                "3717",
                "--output",
                "material-smoke",
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        checks["process_completed"] = True
        checks["return_code_zero"] = process.returncode == 0
        payload.update(return_code=process.returncode, stdout=process.stdout, stderr=process.stderr)
        if process.returncode:
            raise ValueError("Material builder smoke failed")
        result = json.loads(Path("material-smoke/metadata.json").read_text())
        checks["table_written"] = Path("material-smoke/table.cn4").is_file()
        checks["serialization_checked"] = (
            max(result["serialization_max_relative_errors"].values()) < 5.1e-6
        )
        checks["profile_recorded"] = result["profile"]["id"] == "native-feos"
        payload["result"] = result
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        payload["error"] = str(error)
    checks["completed"] = all(checks.values())
    Path("material_eos_capability_smoke.json").write_text(json.dumps(payload, indent=2) + "\n")
    return 0 if checks["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
