"""Build an explicitly selected material model and export EOS-only IONMIX4."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
from scipy.constants import Avogadro

from .backend import K_PER_EV, NativeFEOS, ThomasFermi, digest
from .ionmix import FIELDS, format_e12, read_ionmix4, write_ionmix4
from .profiles import load_profile, read_grid
from .tin import Parameters, TinEOS

DERIVATIVE_STEP = 0.001
WEIGHTS = np.array([1, -8, 8, -1]) / (12 * DERIVATIVE_STEP)
STEPS = (-2, -1, 1, 2)


def paper_components(eos, rho, t):
    total = eos.evaluate(rho, t)
    _, cv, bi, dpi = eos.ions.evaluate(rho, t)
    p, e, _, _ = eos.electrons.evaluate(rho, t)
    pc, ec, _, _ = eos.electrons.evaluate(rho, 0.0)
    tf = [eos.electrons.evaluate(rho, t * (1 + k * DERIVATIVE_STEP)) for k in STEPS]
    dpe = float(np.dot(WEIGHTS, [v[0] for v in tf]) / t)
    cve = float(np.dot(WEIGHTS, [v[3] for v in tf]))
    zz = [eos.electrons.ionization(rho, t * (1 + k * DERIVATIVE_STEP)) for k in STEPS]
    if rho < eos.params.rho0:
        cold_bulk = eos.soft_sphere(rho)[2] - eos.electrons.bulk_modulus(rho, 0.0)
    else:
        eta = rho / eos.params.rho0
        pb = (
            -eos.morse_energy
            * eos.morse_b
            * eos.params.rho0
            / 3
            * eta ** (2 / 3)
            * math.exp(eos.morse_b * (1 - eta ** (-1 / 3)))
        )
        cold_bulk = pb * (2 / 3 + eos.morse_b / 3 * eta ** (-1 / 3))
    return dict(
        pion=total.pressure - (p - pc),
        pele=p - pc,
        eion=total.energy - (e - ec),
        eele=e - ec,
        cvion=cv,
        cvele=cve,
        dpidt=dpi,
        dpedt=dpe,
        zbar=eos.electrons.ionization(rho, t),
        dzdt=float(np.dot(WEIGHTS, zz) / t),
        bulk_Pa=bi + cold_bulk + eos.electrons.bulk_modulus(rho, t),
    )


def native_components(provider, pairs):
    # A bounded batch amortizes FEOS initialization without a process per grid node.
    expanded = []
    for rho, t in pairs:
        expanded.extend(
            [
                (rho, t),
                *[(rho, t * (1 + k * DERIVATIVE_STEP)) for k in STEPS],
                *[(rho * (1 + k * DERIVATIVE_STEP), t) for k in STEPS],
            ]
        )
    result = provider.batch(expanded)
    output = []
    for index, (_, t) in enumerate(pairs):
        rows = result[index * 9 : index * 9 + 9]
        base, dt, dr = rows[0], rows[1:5], rows[5:9]
        state = {
            "bulk_Pa": float(np.dot(WEIGHTS, [r["pressure"] for r in dr])),
            "zbar": base["zbar"],
            "dzdt": float(np.dot(WEIGHTS, [r["zbar"] for r in dt]) / t),
        }
        for short, full in [("i", "ion_"), ("e", "electron_")]:
            suffix = "ion" if short == "i" else "ele"
            state["p" + suffix] = base[full + "pressure"]
            state["e" + suffix] = base[full + "energy"]
            state["cv" + suffix] = float(np.dot(WEIGHTS, [r[full + "entropy"] for r in dt]))
            state["dp" + short + "dt"] = float(
                np.dot(WEIGHTS, [r[full + "pressure"] for r in dt]) / t
            )
        output.append(state)
    return output


def build(
    profile_name,
    grid_path,
    output,
    *,
    database=None,
    material_id=None,
    allow_unstable=False,
    coarsen_temperature=False,
    query=None,
    tf_library=None,
    data=None,
):
    """Do not expose a final output directory until generation and readback succeed."""
    profile, identity = load_profile(profile_name)
    rho_input, t_input = read_grid(grid_path)
    rho, temp = np.array(rho_input) * 1000, np.array(t_input)
    destination = Path(output).resolve()
    if destination.exists():
        raise ValueError("Output already exists; choose a new artifact directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = {k: np.empty((len(rho), len(temp))) for k in (*FIELDS, "bulk_Pa")}
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".material-eos-") as tmp:
        work = Path(tmp)
        if profile["engine"] == "native-feos":
            if database is None or material_id is None:
                raise ValueError("Native FEOS requires an explicit --database and --material-id")
            provider = NativeFEOS(database, material_id, work, query=query, data=data)
            actual = provider.describe(float(rho[0]), float(temp[0]))
            mass, z = actual["atomic_mass"], actual["atomic_number"]
            # A one-element writer must not silently encode a mixture as an element.
            if not 1 <= z <= 118 or not math.isclose(z, round(z), abs_tol=1e-10):
                raise ValueError("This exporter currently supports pure elements only")
            _check_single_element_database(database, material_id, mass, z)
            pairs = [(float(r), float(t)) for r in rho for t in temp]
            for start in range(0, len(pairs), 512):
                for offset, state in enumerate(
                    native_components(provider, pairs[start : start + 512])
                ):
                    for key, value in state.items():
                        raw[key].flat[start + offset] = value
            model_identity = {**provider.identity, "native_reference_state": actual}
            partition = "Native FEOS ions and corrected electrons; bonding belongs to electrons."
        else:
            if database is not None or material_id is not None:
                raise ValueError(
                    "The paper profile uses its own calibration, not a native material ID"
                )
            params = Parameters(**profile["parameters"])
            mass, z = params.atomic_weight, params.z
            electrons = ThomasFermi(mass, z, library=tf_library, data=data)
            try:
                eos = TinEOS(params=params, electrons=electrons)
                for i, r in enumerate(rho):
                    for j, t in enumerate(temp):
                        state = paper_components(eos, float(r), float(t))
                        for key, value in state.items():
                            raw[key][i, j] = value
                model_identity = {**electrons.identity, "calibration": eos.calibration}
            finally:
                electrons.close()
            partition = (
                "TF thermal electrons relative to T=0 at the same density; "
                "ions include the cold remainder."
            )
        if not math.isfinite(mass) or mass <= 0:
            raise ValueError("Invalid atomic mass")
        nion = rho / 1000 / mass * Avogadro
        for i, r in enumerate(rho):
            for key, pkey, dkey in [("deidn", "pion", "dpidt"), ("deedn", "pele", "dpedt")]:
                raw[key][i, :] = (raw[pkey][i, :] - temp * raw[dkey][i, :]) / (r * nion[i])
        checks = _assess(raw, z)
        if not checks["finite"] or not checks["ionization_in_bounds"]:
            raise ValueError("Nonfinite EOS output or invalid ionization; no table exported")
        unsuitable = any(
            checks[k]
            for k in (
                "nonpositive_bulk_nodes",
                "nonpositive_Cv_nodes",
                "negative_pressure_nodes",
                "nonincreasing_energy_intervals",
            )
        )
        if unsuitable and not allow_unstable:
            raise ValueError(
                "Requested domain fails regular-domain checks: "
                + json.dumps(checks)
                + ". Restrict the grid or explicitly use --allow-unstable for research export."
            )
        offsets = {
            key: float(max(0.0, math.ceil((1e5 - raw[key].min()) / 1000) * 1000))
            for key in ("eion", "eele")
        }
        for key, shift in offsets.items():
            raw[key] += shift
        scales = {
            "zbar": 1.0,
            "dzdt": K_PER_EV,
            "pion": 1e-6,
            "pele": 1e-6,
            "dpidt": 1e-6 * K_PER_EV,
            "dpedt": 1e-6 * K_PER_EV,
            "eion": 1e-3,
            "eele": 1e-3,
            "cvion": 1e-3 * K_PER_EV,
            "cvele": 1e-3 * K_PER_EV,
            "deidn": 1e-3,
            "deedn": 1e-3,
        }
        disk = {k: raw[k] * scales[k] for k in FIELDS}
        quantized = {
            k: np.array([float(format_e12(v)) for v in disk[k].ravel()]).reshape(raw[k].shape)
            for k in ("eion", "eele")
        }
        keep = list(range(len(temp)))
        flat = any(np.any(np.diff(a, axis=1) <= 0) for a in quantized.values())
        if flat and coarsen_temperature:
            if checks["nonincreasing_energy_intervals"]:
                raise ValueError(
                    "Cannot coarsen away physical/non-numerical energy nonmonotonicity"
                )
            keep = [0]
            for j in range(1, len(temp)):
                if all(np.all(a[:, j] > a[:, keep[-1]]) for a in quantized.values()):
                    keep.append(j)
            if len(keep) < 2 or keep[-1] != len(temp) - 1:
                raise ValueError(
                    "Monotonic serialization cannot preserve the requested grid endpoints"
                )
        elif flat and not allow_unstable:
            raise ValueError(
                "IONMIX precision creates flat energy intervals; "
                "use --coarsen-temperature-grid or a coarser grid"
            )
        disk = {k: v[:, keep] for k, v in disk.items()}
        path = work / "table.cn4"
        write_ionmix4(path, temp[keep] / K_PER_EV, nion, disk, z=int(round(z)))
        read = read_ionmix4(path)
        for key in ("temps_ev", "number_density_cm3"):
            if np.any(read[key] <= 0) or np.any(np.diff(read[key]) <= 0):
                raise ValueError(f"IONMIX precision collapses the {key} grid; use wider spacing")
        errors = {}
        for key in FIELDS:
            errors[key] = float(
                np.max(abs(read[key] - disk[key]) / np.maximum(abs(disk[key]), 1e-200))
            )
            if errors[key] > 5.1e-6:
                raise ValueError(f"Serialization error exceeds six-digit rounding in {key}")
        serialized_nonincreasing = sum(
            int(np.sum(np.diff(read[k], axis=1) <= 0)) for k in ("eion", "eele")
        )
        if serialized_nonincreasing and not allow_unstable:
            raise ValueError("Serialized component energies do not increase strictly")
        flags = (
            (raw["bulk_Pa"] <= 0).astype(np.uint8)
            | ((raw["cvion"] <= 0) | (raw["cvele"] <= 0)).astype(np.uint8) * 2
            | ((raw["pion"] < 0) | (raw["pele"] < 0)).astype(np.uint8) * 4
        )
        metadata = {
            "schema_version": "1",
            "builder_version": "0.1.0",
            "profile": profile,
            "identity": {
                **identity,
                **model_identity,
                "grid_file_sha256": digest(grid_path),
                "builder_source_sha256": {
                    p.name: digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))
                },
            },
            "physical_validation": "Not established by the builder or its runtime smoke checks",
            "partition": partition,
            "energy_offsets_J_kg": offsets,
            "energy_reference_relation": (
                "Exported component E equals model component E plus its recorded constant offset"
            ),
            "atomic_mass_g_mol": mass,
            "atomic_number": z,
            "Avogadro": Avogadro,
            "K_per_eV": K_PER_EV,
            "format": "IONMIX4 EOS only; zero radiation groups",
            "table_si_units": {
                "rho_kg_m3": "kg/m3",
                "T_K": "K",
                "pion": "Pa",
                "pele": "Pa",
                "eion": "J/kg",
                "eele": "J/kg",
                "cvion": "J/(kg K)",
                "cvele": "J/(kg K)",
                "dpidt": "Pa/K",
                "dpedt": "Pa/K",
                "zbar": "dimensionless",
                "dzdt": "1/K",
                "bulk_Pa": "Pa",
                "deidn": "(J/kg)/(cm^-3)",
                "deedn": "(J/kg)/(cm^-3)",
            },
            "opacity": "Absent; do not use this file as an opacity table",
            "density_range_g_cm3": [float(rho[0] / 1000), float(rho[-1] / 1000)],
            "temperature_range_K": [float(temp[0]), float(temp[-1])],
            "shape_density_temperature": [len(rho), len(keep)],
            "retained_temperature_indices": keep,
            "removed_temperatures_K": [float(t) for i, t in enumerate(temp) if i not in keep],
            "research_domain_override": bool(allow_unstable),
            "checks": checks,
            "serialized_nonincreasing_energy_intervals": serialized_nonincreasing,
            "serialization_max_relative_errors": errors,
            "flag_bits": {
                "1": "Nonpositive isothermal bulk modulus",
                "2": "Nonpositive component Cv",
                "4": "Negative component pressure",
            },
            "derivatives": (
                "Ionic analytic for paper model; otherwise five-point central "
                "finite differences, relative step 0.001"
            ),
            "FLASH_validation": (
                "No FLASH run is implied by export/readback. "
                "Test the target reader/interpolator and solver separately."
            ),
        }
        np.savez_compressed(
            work / "table_si.npz",
            rho_kg_m3=rho,
            T_K=temp[keep],
            flags=flags[:, keep],
            **{k: v[:, keep] for k, v in raw.items()},
        )
        (work / "metadata.json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
        (work / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
        (work / "flash.par.fragment").write_text(
            '# Use an existing compiled species name in place of "targ". EOS only.\n'
            f"ms_targA = {mass:.12g}\nms_targZ = {z:.12g}\n"
            'eos_targEosType = "eos_tab"\neos_targSubType = "ionmix4"\n'
            'eos_targTableFile = "table.cn4"\neos_useLogTables = .false.\n'
            "# No op_* filename is supplied; opacity requires separate data.\n"
        )
        files = sorted(p for p in work.iterdir() if p.is_file())
        (work / "SHA256SUMS").write_text("".join(f"{digest(p)}  {p.name}\n" for p in files))
        # TemporaryDirectory cleanup tolerates the directory's atomic rename.
        work.rename(destination)
    return metadata


def _assess(raw, z):
    return {
        "finite": bool(all(np.isfinite(a).all() for a in raw.values())),
        "ionization_in_bounds": bool(np.all((raw["zbar"] >= 0) & (raw["zbar"] <= z))),
        "nonpositive_bulk_nodes": int(np.sum(raw["bulk_Pa"] <= 0)),
        "nonpositive_Cv_nodes": int(np.sum((raw["cvion"] <= 0) | (raw["cvele"] <= 0))),
        "negative_pressure_nodes": int(np.sum((raw["pion"] < 0) | (raw["pele"] < 0))),
        "nonincreasing_energy_intervals": sum(
            int(np.sum(np.diff(raw[k], axis=1) <= 0)) for k in ("eion", "eele")
        ),
    }


def _check_single_element_database(path, material_id, mass, z):
    # Effective Z can be an integer even for a mixture. Check the actual deck.
    import re

    text = Path(path).read_text()
    prefix = re.escape(f"[{material_id}]_")

    def value(key):
        match = re.search(r"^\s*" + prefix + re.escape(key) + r"\s*=\s*([^\s%]+)", text, re.M)
        if not match:
            raise ValueError(f"Missing material field {key}")
        return float(match[1])

    if value("Number-of-Elements") != 1:
        raise ValueError(
            "IONMIX export currently requires a single-element material database entry"
        )
    if not math.isclose(value("A[1]"), mass, rel_tol=1e-10) or value("Z[1]") != z:
        raise ValueError("Queried material identity disagrees with the supplied database")
