"""Large builds and director adjustments through the actual experiment launcher."""

import importlib.util
import json
import os
from types import SimpleNamespace

import pytest

from conjecture_solver.experiment_executor import execute_frozen
from conjecture_solver.research_service import ResearchService, sha
from tests.test_research_director import active, supervisor


def test_generated_registration_client_serializes_name_and_machine(tmp_path, monkeypatch):
    sup = supervisor(tmp_path)
    spec = importlib.util.spec_from_file_location("generated_lab", sup.service.work / "lab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, json.loads(kwargs["input"])))
        return SimpleNamespace(stdout=json.dumps({"ok": True, "result": {"name": "new--at-gpu"}}))

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.lab.register_capability("new", machine="gpu")["name"] == "new--at-gpu"
    assert calls[0][0][-1] == "register_capability"
    assert calls[0][1] == {"name": "new", "machine": "gpu"}


@pytest.mark.skipif(os.geteuid() == 0, reason="Native execution requires an unprivileged account")
@pytest.mark.parametrize("allowance,large_file,blocked", [(768, 600, False), (16, 32, True)])
def test_real_file_limit_follows_workspace_allowance(tmp_path, allowance, large_file, blocked):
    source = tmp_path / "build_probe.py"
    source.write_text(
        "import os,json,resource,errno\nfrom pathlib import Path\n"
        "limit=resource.getrlimit(resource.RLIMIT_FSIZE)[0]\nblocked=False\n"
        "with open('sparse-build.bin','wb') as f:\n"
        " try:\n"
        f"  f.truncate({large_file}*1024**2)\n"
        " except OSError as e:\n"
        "  assert e.errno==errno.EFBIG\n  blocked=True\n"
        "size=Path('sparse-build.bin').stat().st_size\n"
        "Path('sparse-build.bin').unlink()\n"
        "Path('result.json').write_text(json.dumps(dict(limit=limit,size=size,blocked=blocked)))\n"
    )
    receipt = execute_frozen(
        tmp_path,
        dict(source=source.name, args=[], inputs={source.name: sha(source)}, capability=None),
        ["result.json"],
        timeout=15,
        execution_backend="process-cooperative",
        max_workspace_bytes=allowance * 1024**2,
    )
    assert receipt["status"] == "succeeded", receipt
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["limit"] == allowance * 1024**2
    assert result["blocked"] is blocked
    if not blocked:
        assert result["size"] == large_file * 1024**2
    assert receipt["execution"]["resource_limits"]["max_file_bytes"] == allowance * 1024**2


def increase(experiment=32, total=256):
    return dict(
        max_experiment_bytes=experiment * 1024**3,
        max_total_bytes=total * 1024**3,
        reason="The retained binary and checkpoint outputs exceed the current allocation.",
    )


def test_storage_delegation_is_required_and_not_worker_callable(tmp_path):
    service = ResearchService.create(tmp_path / "study", "Original scientific hypothesis")
    with pytest.raises(ValueError, match="not delegated"):
        service.increase_storage(increase(), director_decision="director_one")
    service.configure_resource_policy(
        director_can_increase=True, reason="Dedicated operator-owned machine"
    )
    with pytest.raises(ValueError, match="immutable"):
        service.configure_resource_policy(director_can_increase=False, reason="Changed delegation")
    from conjecture_solver.research_service import Lab

    assert not hasattr(Lab, "increase_storage")


def test_storage_history_preserves_original_jobs_and_manifest(tmp_path, monkeypatch):
    service = ResearchService.create(tmp_path / "study", "Original scientific hypothesis")
    service.configure_resource_policy(
        director_can_increase=True, reason="User authorized dedicated storage"
    )
    (service.work / "calc.py").write_text("print(1)\n")
    monkeypatch.setattr(service, "_spawn_experiment", lambda record: None)
    original_manifest = (service.root / "research.json").read_bytes()
    first = service.run("calc.py", outputs=["result.json"], stage="exploration", key="first")
    grant = service.increase_storage(increase(), director_decision="director_one")
    assert service.increase_storage(increase(), director_decision="director_one") == grant
    assert ResearchService(service.root).storage_limits() == grant["after"]
    second = service.run("calc.py", outputs=["result.json"], stage="exploration", key="second")
    assert first["workspace_limit_bytes"] == 4 * 1024**3
    assert second["workspace_limit_bytes"] == 32 * 1024**3
    assert service._read("experiments", first["id"])["workspace_limit_bytes"] == 4 * 1024**3
    assert (service.root / "research.json").read_bytes() == original_manifest
    service.increase_storage(increase(1024, 2048), director_decision="director_two")
    assert service.storage_limits()["max_experiment_bytes"] == 1024 * 1024**3
    with pytest.raises(ValueError, match="cannot reduce"):
        service.increase_storage(increase(), director_decision="director_three")
    with pytest.raises(ValueError, match="different allowance"):
        service.increase_storage(increase(64), director_decision="director_one")


def test_director_storage_grant_wakes_worker_and_reports_it(tmp_path, monkeypatch):
    sup = supervisor(tmp_path)
    active(sup, monkeypatch)
    sup.service.configure_resource_policy(director_can_increase=True, reason="Dedicated machine")
    verdict = dict(
        decision="continue",
        rationale="A larger build allowance resolves the recorded linker blocker.",
        scientific_feasibility="unknown",
        budget_feasibility="feasible",
        stop_experiments=[],
        next_action="Repeat the failed linker operation through a recorded experiment.",
        storage_increase=increase(),
    )

    def launch(directory, prompt, **kwargs):
        assert "director_can_increase" in prompt
        (directory / "response.json").write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": json.dumps(verdict)},
                }
            )
            + "\n"
            + json.dumps({"type": "turn.completed", "usage": {}})
            + "\n"
        )
        return 0

    monkeypatch.setattr(sup, "launch", launch)
    assert sup.run_director(force=True)
    assert sup.service.director_status()[0]["storage_action"]["status"] == "granted"
    assert sup.state["director_wake_worker"]
    assert sup.service.status()["resource_limits"]["max_experiment_bytes"] == 32 * 1024**3
    assert len(sup.service.status()["storage_adjustments"]) == 1
