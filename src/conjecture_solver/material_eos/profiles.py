"""Versioned model profiles; a profile is a model choice, not a material qualification."""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

from .backend import digest
from .tin import Parameters

PROFILE_ROOT = Path(__file__).parent / "profiles"


def load_profile(value):
    path = Path(value)
    if not path.is_file():
        if Path(value).name != str(value):
            raise ValueError("Profile file does not exist")
        path = PROFILE_ROOT / (str(value) + ".json")
    if not path.is_file():
        raise ValueError(
            f"Unknown material profile: {value}. Use 'profiles' to list available recipes."
        )
    profile = json.loads(path.read_text())
    required = {
        "schema_version",
        "id",
        "version",
        "engine",
        "scientific_status",
        "sources",
        "parameters",
        "modifications",
        "limitations",
    }
    if (
        not isinstance(profile, dict)
        or set(profile) != required
        or profile["schema_version"] != "1"
    ):
        raise ValueError("Profile keys/schema do not match material-EOS profile version 1")
    if profile["engine"] not in ("native-feos", "tin-ding-2026"):
        raise ValueError("Unsupported profile engine")
    for key in ("id", "version", "scientific_status"):
        if not isinstance(profile[key], str) or not profile[key].strip():
            raise ValueError(f"Profile {key} must be a nonempty string")
    for key in ("sources", "modifications", "limitations"):
        if not isinstance(profile[key], list) or any(not isinstance(v, str) for v in profile[key]):
            raise ValueError(f"Profile {key} must contain strings")
    if not profile["sources"]:
        raise ValueError("Material profile requires parameter/model sources")
    p = profile["parameters"]
    if profile["engine"] == "native-feos":
        if p != {}:
            raise ValueError("Native FEOS parameters come from the explicit material database")
    else:
        if not isinstance(p, dict) or set(p) != {f.name for f in dataclasses.fields(Parameters)}:
            raise ValueError("The paper profile must explicitly supply every model parameter")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in p.values()):
            raise ValueError("Paper parameters must be finite numbers")
        if p["z"] != 50 or p["atomic_weight"] != 118.71:
            raise ValueError("The tin-paper engine cannot be relabeled as another element")
        positive = set(p) - {"gamma_prime_left", "gamma_prime_right"}
        if any(p[k] <= 0 for k in positive) or not 0 < p["alpha"] < 1:
            raise ValueError("Invalid positive material parameter or liquid exponent")
    return profile, {"profile_file_sha256": digest(path)}


def read_grid(path):
    obj = json.loads(Path(path).read_text())
    if not isinstance(obj, dict) or set(obj) != {"density_g_cm3", "temperature_K"}:
        raise ValueError("Grid must contain density_g_cm3 and temperature_K arrays")
    axes = []
    for name in ("density_g_cm3", "temperature_K"):
        values = obj[name]
        if not isinstance(values, list) or len(values) < 2:
            raise ValueError(f"Grid {name} needs at least two values")
        if any(type(v) not in (float, int) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("Grid entries must be finite positive numbers")
        if any(b <= a for a, b in zip(values[:-1], values[1:], strict=True)):
            raise ValueError("Grid coordinates must increase strictly")
        axes.append(values)
    if len(axes[0]) * len(axes[1]) > 1_000_000:
        raise ValueError("Grid exceeds the one-million-state limit; split the requested grid")
    return axes
