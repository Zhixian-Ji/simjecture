"""FEOS installer trust checks and query-adapter failure/field mapping regressions."""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def installer():
    path = ROOT / "skills/eos/scripts/bootstrap_feos.py"
    spec = importlib.util.spec_from_file_location("bootstrap_feos", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bad_archive_is_rejected_before_extracting_or_building(tmp_path, monkeypatch):
    module = installer()
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"bad"))
    with pytest.raises(ValueError, match="checksum"):
        module.acquire_source(tmp_path)
    assert not (tmp_path / "src/FEOS").exists()
    assert not (tmp_path / "src/FEOS.tar.gz").exists()


def test_local_source_identity_ignores_build_outputs_but_rejects_modified_source(
    tmp_path, monkeypatch,
):
    module = installer()
    for folder in ("Code", "EOS-Data", "Documents"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "input.txt").write_text(folder)
    inventory = {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in tmp_path.rglob("input.txt")}
    digest = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    monkeypatch.setattr(module, "TREE_SHA256", digest)
    (tmp_path / "Code/compiled.o").write_bytes(b"existing build")
    assert module.source_inventory(tmp_path) == inventory
    (tmp_path / "Code/input.txt").write_text("changed physics")
    with pytest.raises(ValueError, match="does not match"):
        module.source_inventory(tmp_path)


@pytest.fixture(scope="module")
def query(tmp_path_factory):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("C++ compiler required for adapter boundary tests")
    directory = tmp_path_factory.mktemp("feos-adapter")
    # Deliberately distinct component values exercise positional mapping. This
    # is an interface fixture, never numerical FEOS qualification.
    (directory / "libfeos.h").write_text('extern "C" {\n'
        'void FEOS_Initialize(int,int); void FEOS_Finalize();\n'
        'void FEOS_Init_Mat(int,int,int,int,double*,int,int*,int*,double*);\n'
        'void FEOS_Get_Mat_Par(int,double*,double*,double*,double*,double*,double*,\n'
        'double*,double*,double*,int*);\n'
        'void FEOS_Get_Energy_Offsets(int,double*,double*);\n'
        'void FEOS_Get_EOS_All(int,int,double,double,int*,\n'
        'double*,double*,double*,double*,double*,double*,\n'
        'double*,double*,double*,double*,double*,double*,double*,double*,\n'
        'double*,double*,double*,double*);\n}\n')
    (directory / "fixture.cpp").write_text('''
#include "libfeos.h"
#include <cstdlib>
#include <limits>
extern "C" {
void FEOS_Initialize(int,int) { if(std::getenv("TEST_UPSTREAM_EXIT")) std::exit(0); }
void FEOS_Finalize() {}
void FEOS_Init_Mat(int,int,int,int,double*,int,int*n,int*m,double*r) {*n=1;*m=0;*r=0;}
void FEOS_Get_Mat_Par(int,double*a,double*z,double*x,double*at,double*zt,double*xt,
 double*t,double*r,double*b,int*s) {*a=*at=183.84;*z=*zt=74;*x=*xt=1;
 *t=.025;*r=19.25;*b=3.1e12;*s=3550;}
void FEOS_Get_Energy_Offsets(int,double*e,double*i) {*e=12;*i=34;}
void FEOS_Get_EOS_All(int,int,double rho,double t,int*phase,
 double*p,double*e,double*s,double*f,double*q,double*qt,
 double*pe,double*ee,double*se,double*fe,double*pi,double*ei,double*si,double*fi,
 double*pt,double*et,double*st,double*ft) {
 *phase=0;*p=rho;*e=t;*s=3;*f=4;*q=*qt=5;
 *pe=6;*ee=7;*se=8;*fe=9;*pi=10;*ei=11;*si=12;*fi=13;
 *pt=14;*et=15;*st=16;*ft=17;
 if(std::getenv("TEST_NONFINITE")) *ee=std::numeric_limits<double>::quiet_NaN();
}
}
''')
    binary = directory / "feos-query"
    subprocess.run([compiler, "-std=c++17", "-I" + str(directory),
                    str(ROOT / "skills/eos/scripts/feos_query.cpp"),
                    str(directory / "fixture.cpp"), "-o", str(binary)], check=True)
    return binary


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "FEOS_Material-DB.dat").write_text("Material-3550:\n")
    (tmp_path / "FEOS_TF-Table_1197.dat").write_text("fixture")
    return tmp_path


def test_single_and_batch_queries_preserve_component_mapping(query, workspace):
    subprocess.run([query, "3550", "20", "250", "result.json"], cwd=workspace, check=True)
    result = json.loads((workspace / "result.json").read_text())
    assert result["pressure"] == 20 and result["energy"] == 250
    assert result["electron_energy"] == 7 and result["ion_energy"] == 11
    assert result["tf_free_energy"] == 17 and result["zbar"] == 5
    assert result["units"]["entropy"] == "erg/(g eV)"
    assert not result["maxwell"] and not result["soft_sphere"]
    (workspace / "states.csv").write_text("rho_g_cm3,temperature_eV\n20,250\n22,500\n")
    subprocess.run([query, "--batch", "3550", "states.csv", "result.csv"],
                   cwd=workspace, check=True)
    import csv

    rows = list(csv.DictReader((workspace / "result.csv").open()))
    assert len(rows) == 2 and float(rows[1]["energy"]) == 500
    assert float(rows[0]["electron_energy"]) == 7


@pytest.mark.parametrize("mode", ["TEST_UPSTREAM_EXIT", "TEST_NONFINITE"])
def test_upstream_zero_exit_and_nonfinite_values_are_failures(query, workspace, mode):
    run = subprocess.run([query, "3550", "20", "250", "result.json"], cwd=workspace,
                         env=os.environ | {mode: "1"}, capture_output=True, text=True)
    assert run.returncode != 0
    assert "before" in run.stderr if mode == "TEST_UPSTREAM_EXIT" else "Nonfinite" in run.stderr


@pytest.mark.parametrize("rho,t", [("nan", "1"), ("1", "inf"), ("0", "1"), ("-1", "1")])
def test_invalid_states_are_rejected(query, workspace, rho, t):
    run = subprocess.run([query, "3550", rho, t, "result.json"], cwd=workspace)
    assert run.returncode != 0 and not (workspace / "result.json").exists()
