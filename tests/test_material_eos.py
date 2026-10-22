"""Material construction, provenance, and serialization tests; fixtures are nonphysical."""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.integrate import quad

from conjecture_solver.material_eos.backend import K_PER_EV, ThomasFermi
from conjecture_solver.material_eos.builder import build, paper_components
from conjecture_solver.material_eos.ionmix import read_ionmix4
from conjecture_solver.material_eos.profiles import PROFILE_ROOT, load_profile, read_grid
from conjecture_solver.material_eos.tin import Ionic, Parameters, TinEOS, debye3

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def native_fixture(tmp_path):
    # Analytic ideal components test the transport and units, never FEOS accuracy.
    query = tmp_path / "query"
    query.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """
import csv,json,math,sys
def state(r,t):
    out={'rho_g_cm3':r,'temperature_eV':t,'zbar':5.}
    for prefix,R,shift in [('ion_',1e7,-1e6),('electron_',2e7,-2e6)]:
        P=r*R*t; E=1.5*R*t+shift; S=R*(1.5*math.log(t)-math.log(r))
        out.update({prefix+'pressure':P,prefix+'energy':E,prefix+'entropy':S,
                    prefix+'free_energy':E-t*S})
    for key in ['pressure','energy','entropy','free_energy']:
        out[key]=out['ion_'+key]+out['electron_'+key]
        out['tf_'+key]=out['electron_'+key]
    return out
if sys.argv[1]=='--batch':
    with open(sys.argv[3]) as f:
        rows=[state(float(r['rho_g_cm3']),float(r['temperature_eV'])) for r in csv.DictReader(f)]
    with open(sys.argv[4],'w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
else:
    out=state(float(sys.argv[2]),float(sys.argv[3]))
    out.update(atomic_mass=10.,atomic_number=5.,reference_density=1.,
               reference_temperature=.025,reference_bulk_modulus=1e11,
               electron_energy_offset=2e6,ion_energy_offset=1e6)
    with open(sys.argv[4],'w') as f: json.dump(out,f)
"""
    )
    query.chmod(0o755)
    database = tmp_path / "db.dat"
    database.write_text(
        "Material-1234:\n[1234]_Number-of-Elements = 1\n[1234]_A[1] = 10\n[1234]_Z[1] = 5\n"
    )
    data = tmp_path / "data"
    data.mkdir()
    (data / "FEOS_TF-Table_1197.dat").write_text("explicit nonphysical fixture\n")
    grid = tmp_path / "grid.json"
    grid.write_text(
        json.dumps({"density_g_cm3": [0.1, 1.0], "temperature_K": [1000.0, 2000.0, 4000.0]})
    )
    return dict(database=database, material_id=1234, query=query, data=data), grid


def test_native_builder_units_component_identity_and_provenance(tmp_path, native_fixture):
    kwargs, grid = native_fixture
    metadata = build("native-feos", grid, tmp_path / "out", **kwargs)
    table = read_ionmix4(tmp_path / "out/table.cn4")
    assert metadata["shape_density_temperature"] == [2, 3]
    assert metadata["atomic_number"] == 5
    assert metadata["profile"]["id"] == "native-feos"
    assert "Not established" in metadata["physical_validation"]
    assert metadata["serialized_nonincreasing_energy_intervals"] == 0
    assert metadata["identity"]["database_sha256"]
    assert metadata["identity"]["builder_source_sha256"]
    rho = np.array([0.1, 1.0])[:, None]
    tev = np.array([1000.0, 2000.0, 4000.0])[None, :] / K_PER_EV
    np.testing.assert_allclose(table["pion"], rho * tev, rtol=5e-6)
    np.testing.assert_allclose(table["pele"], 2 * rho * tev, rtol=5e-6)
    np.testing.assert_allclose(table["cvion"], 1.5, rtol=5e-6)
    np.testing.assert_allclose(table["cvele"], 3.0, rtol=5e-6)
    for key, factor, model_shift in [("eion", 1.5, -0.1), ("eele", 3.0, -0.2)]:
        offset = metadata["energy_offsets_J_kg"][key] * 1e-3
        expected = np.broadcast_to(factor * tev + model_shift + offset, (2, 3))
        np.testing.assert_allclose(table[key], expected, rtol=5e-6)
    with pytest.raises(ValueError, match="already exists"):
        build("native-feos", grid, tmp_path / "out", **kwargs)


def test_explicit_material_selection_and_mixture_rejection(tmp_path, native_fixture):
    kwargs, grid = native_fixture
    with pytest.raises(ValueError, match="explicit"):
        build("native-feos", grid, tmp_path / "absent")
    assert not (tmp_path / "absent").exists()
    kwargs["database"].write_text(
        kwargs["database"].read_text().replace("Elements = 1", "Elements = 2")
    )
    with pytest.raises(ValueError, match="single-element"):
        build("native-feos", grid, tmp_path / "mixture", **kwargs)
    assert not (tmp_path / "mixture").exists()


def test_serialization_cannot_hide_collapsed_density_grid(tmp_path, native_fixture):
    kwargs, grid = native_fixture
    grid.write_text(
        json.dumps({"density_g_cm3": [1.0, 1.000000001], "temperature_K": [1000.0, 2000.0]})
    )
    with pytest.raises(ValueError, match="collapses"):
        build("native-feos", grid, tmp_path / "out", allow_unstable=True, **kwargs)
    assert not (tmp_path / "out").exists()


def test_precision_coarsening_is_explicit_and_records_removed_temperatures(
    tmp_path, native_fixture
):
    kwargs, grid = native_fixture
    grid.write_text(
        json.dumps({"density_g_cm3": [0.1, 1.0], "temperature_K": [1000.0, 1000.000001, 2000.0]})
    )
    with pytest.raises(ValueError, match="flat energy"):
        build("native-feos", grid, tmp_path / "out", **kwargs)
    metadata = build("native-feos", grid, tmp_path / "out", coarsen_temperature=True, **kwargs)
    assert metadata["removed_temperatures_K"] == [1000.000001]
    assert metadata["retained_temperature_indices"] == [0, 2]
    assert metadata["serialized_nonincreasing_energy_intervals"] == 0


@pytest.mark.parametrize(
    "density,temp",
    [
        ([0.0, 1.0], [1.0, 2.0]),
        ([1.0, 1.0], [1.0, 2.0]),
        ([1.0, 2.0], [math.nan, 2.0]),
        ([1.0], [1.0, 2.0]),
    ],
)
def test_invalid_grid_rejected(tmp_path, density, temp):
    path = tmp_path / "grid.json"
    path.write_text(json.dumps(dict(density_g_cm3=density, temperature_K=temp)))
    with pytest.raises(ValueError):
        read_grid(path)


def test_profile_requires_complete_parameters_and_prevents_element_relabeling(tmp_path):
    profile, _ = load_profile("tin-ding-2026-reconstruction")
    path = tmp_path / "profile.json"
    profile["parameters"]["z"] = 74
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="relabeled"):
        load_profile(path)
    profile["parameters"]["z"] = 50
    del profile["parameters"]["gamma_ref"]
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="every model parameter"):
        load_profile(path)
    with pytest.raises(ValueError, match="Unknown material profile"):
        load_profile("unsupported-element")


def test_debye_and_ionic_free_energy_derivatives():
    ions = Ionic()
    for u in [0.001, 0.1, 1.0, 5.0, 50.0, 100.0]:
        exact = 3 / u**3 * quad(lambda x: x**3 / math.expm1(x), 0, u)[0]
        assert debye3(u) == pytest.approx(exact, rel=1e-9)
    for rho, t in [(7500.0, 100.0), (10000.0, 300.0), (7600.0, 1000.0), (1000.0, 1e5)]:
        s, cv, bulk, dpdt = ions.evaluate(rho, t)
        h = 1e-5
        rp = ions.evaluate(rho * (1 + h), t)[0]
        rm = ions.evaluate(rho * (1 - h), t)[0]
        tp = ions.evaluate(rho, t * (1 + h))[0]
        tm = ions.evaluate(rho, t * (1 - h))[0]
        assert rho * (rp.free_energy - rm.free_energy) / (2 * h) == pytest.approx(
            s.pressure, rel=1e-7
        )
        assert (tp.energy - tm.energy) / (2 * h * t) == pytest.approx(cv, rel=1e-7)
        assert (rp.pressure - rm.pressure) / (2 * h) == pytest.approx(bulk, rel=1e-7)
        assert (tp.pressure - tm.pressure) / (2 * h * t) == pytest.approx(dpdt, rel=1e-7)
        assert s.free_energy + t * s.entropy == pytest.approx(s.energy, rel=1e-12)


def test_paper_profile_parameter_traceability():
    profile, _ = load_profile("tin-ding-2026-reconstruction")
    assert profile["scientific_status"] == "research-reconstruction"
    assert profile["sources"] and profile["modifications"] and profile["limitations"]
    p = Parameters(**profile["parameters"])
    ions = Ionic(p)
    target = [223.33, 209.22, 195.86, 183.22, 171.28, 160.0, 149.36, 139.35, 129.97, 121.20, 113.02]
    prediction = [ions.vibration(p.rho0 / s**3)[0] for s in np.arange(95, 106) / 100]
    assert np.sqrt(np.mean((np.array(prediction) - target) ** 2)) < 0.003


@pytest.mark.skipif(not os.environ.get("MATERIAL_EOS_LIVE"), reason="real FEOS runtime is optional")
def test_live_paper_calibration_and_IONMIX_build(tmp_path):
    profile, _ = load_profile("tin-ding-2026-reconstruction")
    params = Parameters(**profile["parameters"])
    tf = ThomasFermi(params.atomic_weight, params.z)
    try:
        eos = TinEOS(params=params, electrons=tf)
        for branch in ("low", "high"):
            state = eos.evaluate(7550.0, 300.0, branch)
            assert abs(state.pressure) < 1.0
            assert abs(state.energy) < 0.01
        c = paper_components(eos, 15000.0, 20000.0)
        expected = eos.evaluate(15000.0, 20000.0)
        assert c["pion"] + c["pele"] == pytest.approx(expected.pressure, rel=1e-12)
        assert c["eion"] + c["eele"] == pytest.approx(expected.energy, rel=1e-12)
        # Independent golden values retained from the earlier standalone reconstruction.
        assert eos.evaluate(10000.0, 300.0).pressure / 1e9 == pytest.approx(33.5603981461, rel=2e-5)
        shock = eos.hugoniot(15000.0)
        assert shock["P_Pa"] / 1e9 == pytest.approx(324.535399974, rel=2e-5)
    finally:
        tf.close()
    grid = tmp_path / "grid.json"
    grid.write_text(
        json.dumps(
            {"density_g_cm3": [1.0, 7.55, 15.0], "temperature_K": [20000.0, 40000.0, 80000.0]}
        )
    )
    metadata = build("tin-ding-2026-reconstruction", grid, tmp_path / "table")
    assert metadata["checks"]["nonpositive_bulk_nodes"] == 0
    assert metadata["serialized_nonincreasing_energy_intervals"] == 0
    assert metadata["profile"]["scientific_status"] == "research-reconstruction"


def test_cli_error_has_no_success_receipt(tmp_path):
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "conjecture_solver.material_eos",
            "build",
            "--profile",
            "unknown",
            "--grid",
            "absent.json",
            "--output",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        env=os.environ | {"PYTHONPATH": str(ROOT / "src")},
    )
    assert run.returncode != 0
    assert json.loads(run.stderr)["completed"] is False
    assert not (tmp_path / "out").exists()


def test_capability_identity_covers_all_installed_model_code_and_profiles():
    descriptor = json.loads((ROOT / "capabilities/material-eos-0.1.json").read_text())
    package = PROFILE_ROOT.parent
    for path in package.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".json"):
            assert (
                "share/material_eos/" + str(path.relative_to(package))
                in descriptor["identity_files"]
            )
