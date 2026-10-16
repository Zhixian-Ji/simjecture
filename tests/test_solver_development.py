import argparse
import json
import sys
from types import SimpleNamespace

import pytest

from conjecture_solver.research_service import ResearchService, put
from conjecture_solver.study import configure_parser, run
from conjecture_solver.study_launch import NativeStudyRequest, materialize_native, resume_native
from tests.test_research_director import supervisor


def provenance():
    return dict(
        parent_capability="reference",
        change_type="numerical",
        rationale="Correct the observed numerical transport error in an experimental fork.",
        preserved_assumptions="Keep the equations, initial conditions and physical coefficients.",
        validation_plan="Compare actual evolution and conservation at two numerical resolutions.",
    )


def registry_service(tmp_path, monkeypatch, *, allowed=True):
    service = ResearchService.create(
        tmp_path / "study", "Fixed hypothesis", allow_solver_development=allowed
    )
    service.manifest.update(
        capabilities=str(tmp_path / "registry"), capability_hashes={"reference": "old"}
    )
    put(service.root / "research.json", service.manifest)
    hashes = {"reference": "old", "candidate": "new"}
    monkeypatch.setattr(
        "conjecture_solver.research_methods.MVPCapabilityRegistry.discover",
        lambda p, *, names=None: SimpleNamespace(
            get=lambda name: SimpleNamespace(contract_hash=hashes[name])
        ),
    )
    (service.work / "calc.py").write_text("print(1)\n")
    monkeypatch.setattr(service, "_spawn_experiment", lambda record: None)
    return service


def test_new_default_and_off_are_frozen_without_changing_deadline(tmp_path):
    s = ResearchService.create(tmp_path / "allowed", "Fixed hypothesis")
    assert s.solver_development_policy()["allowed"] is True
    off = ResearchService.create(
        tmp_path / "off", "Fixed hypothesis", allow_solver_development=False
    )
    before = (off.root / "research.json").read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        ResearchService.create(off.root, "Fixed hypothesis", allow_solver_development=True)
    assert (off.root / "research.json").read_bytes() == before
    with pytest.raises(ValueError, match="boolean"):
        ResearchService.create(off.root, "Fixed hypothesis", allow_solver_development=0)


def test_disabled_keeps_reference_and_experiment_scripting_available(tmp_path, monkeypatch):
    s = registry_service(tmp_path, monkeypatch, allowed=False)
    assert s.register_capability("reference")["runtime_sha256"] == "old"
    with pytest.raises(ValueError, match="development is disabled"):
        s.register_capability("candidate", development=provenance())
    assert not s._all("capability_additions")
    assert s.analyze("calc.py", outputs=["result.json"])["status"] == "queued"


def test_build_provenance_is_immutable_and_new_instrument_needs_review(tmp_path, monkeypatch):
    s = registry_service(tmp_path, monkeypatch)
    added = s.register_capability("candidate", development=provenance())
    assert added["development"]["change_type"] == "numerical"
    assert s.register_capability("candidate", development=provenance())["runtime_sha256"] == "new"
    with pytest.raises(ValueError, match="provenance is immutable"):
        s.register_capability("candidate", development=provenance() | {"change_type": "physical"})
    assert s.manifest["methods_required"] is False
    with pytest.raises(ValueError, match="Submit lab.method"):
        s.run("calc.py", capability="candidate", outputs=["result.json"])
    assert (
        s.run("calc.py", capability="candidate", outputs=["result.json"], stage="exploration")[
            "status"
        ]
        == "queued"
    )
    m = s.method(
        source="calc.py",
        capability="candidate",
        model="Unchanged equations",
        geometry="Finite test domain",
        observable="Defined numerical measurement",
        validation="Independent evolution and conservation controls",
        rationale="Use the separately built numerical repair after qualification",
    )
    assert m["solver_development"]["registration"]["development"] == added["development"]
    m.update(
        status="resolved",
        verdict={"decision": "continue", "prerequisites": [], "evidence_requests": []},
    )
    put(s.root / "methods" / (m["id"] + ".json"), m)
    assert (
        s.run("calc.py", capability="candidate", outputs=["result.json"], method=m["id"])["status"]
        == "queued"
    )
    view = s.solver_development_status()["builds"][0]
    assert view["method_reviews"][0]["scope"] == "production"
    assert (s.root / view["registration_path"]).is_file()


def test_unknown_parent_and_running_build_are_rejected(tmp_path, monkeypatch):
    s = registry_service(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="registered parent"):
        s.register_capability(
            "candidate", development=provenance() | {"parent_capability": "absent"}
        )
    put(s.root / "experiments/exp_build.json", {"id": "exp_build", "status": "running"})
    with pytest.raises(ValueError, match="Wait for the recorded build"):
        s.register_capability(
            "candidate", development=provenance() | {"build_experiments": ["exp_build"]}
        )


@pytest.mark.parametrize("allowed", [True, False])
def test_cli_launch_and_resume_preserve_permission(tmp_path, monkeypatch, allowed):
    h, i = tmp_path / "h", tmp_path / "i"
    h.write_text("Fixed hypothesis")
    i.write_text("Respect the operator scientific constraints.")
    parser = argparse.ArgumentParser()
    configure_parser(parser)
    args = parser.parse_args(
        [
            "--campaign",
            str(tmp_path / "study"),
            "--hypothesis-file",
            str(h),
            "--instructions-file",
            str(i),
            "--executable",
            sys.executable,
            "--allow-solver-development" if allowed else "--no-allow-solver-development",
        ]
    )
    monkeypatch.setattr(
        "conjecture_solver.research_supervisor.ResearchSupervisor.run", lambda self: 0
    )
    assert run(args) == 0
    s = ResearchService(args.campaign)
    assert s.solver_development_policy()["allowed"] is allowed
    plan = resume_native(s.root)
    assert plan.request.allow_solver_development is allowed
    assert (
        "--allow-solver-development" if allowed else "--no-allow-solver-development"
    ) in plan.argv
    assert ("ALLOWED" if allowed else "DISABLED") in (s.work / "RESEARCH_GUIDE.md").read_text()


def test_legacy_resume_does_not_infer_new_permissions(tmp_path):
    request = NativeStudyRequest(
        hypothesis="Fixed hypothesis",
        campaign_id="study",
        output_directory=str(tmp_path / "study"),
        agent_executable=sys.executable,
    )
    materialize_native(request)
    s = ResearchService(request.output_directory)
    manifest = s.manifest.copy()
    manifest.pop("solver_development_policy")
    put(s.root / "research.json", manifest)
    path = s.root / "study-launch.json"
    saved = json.loads(path.read_text())
    saved["request"].pop("allow_solver_development")
    put(path, saved)
    before = (s.root / "research.json").read_bytes()
    resumed = resume_native(s.root)
    assert resumed.request.allow_solver_development is None
    assert "--allow-solver-development" not in resumed.argv
    assert ResearchService(s.root).solver_development_policy()["allowed"] is None
    assert (s.root / "research.json").read_bytes() == before


def test_worker_director_and_reviewer_see_same_policy(tmp_path):
    sup = supervisor(tmp_path)
    packet = sup.director_packet()
    assert packet["solver_development"]["policy"]["allowed"] is True
    assert "solver development is ALLOWED" in sup.director_prompt(packet)
    assert "solver development is ALLOWED" in (sup.service.work / "RESEARCH_GUIDE.md").read_text()
    assert "source-development permission separate from evidence approval" in sup.oversight_prompt(
        {}
    )
