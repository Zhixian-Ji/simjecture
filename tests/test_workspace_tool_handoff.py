"""Scientific tool selections survive drafting and reach the frozen study contract."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conjecture_solver.mvp_skills import MVPCapabilityInstallation
from conjecture_solver.research_service import put
from conjecture_solver.web.application import SimjectureWebApplication
from conjecture_solver.web.workspace import Workspace, load


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / ".workspace")
    workspace.__dict__["execution"] = {"backend": "process-cooperative"}
    project = workspace.create({"name": "Solver study"})
    descriptors = tmp_path / "capabilities"
    descriptors.mkdir()
    cards = []
    for name in ("flash", "other"):
        runtime = tmp_path / name
        runtime.mkdir()
        runner = runtime / "runner"
        runner.write_text("#!/usr/bin/sh\nexit 0\n")
        runner.chmod(0o755)
        descriptor = descriptors / f"{name}.json"
        put(descriptor, {
            "runtime_root": str(runtime),
            "executable": "runner",
            "manifest": {
                "name": name + "-fixture",
                "version": "1",
                "description": "Test instrument",
                "skill": "flash-mhd",
                "executable_kind": "test",
            },
        })
        cards.append({
            "id": name, "name": name, "installed": True, "path": str(descriptor),
        })
    monkeypatch.setattr(workspace, "catalogue", lambda: cards)
    payload = {
        "question": "Test the declared solver.",
        "success_criteria": "A qualified trajectory.",
        "constraints": "Keep the forcing fixed.",
        "hours": 24,
    }
    return workspace, project["id"], payload, cards


@pytest.mark.parametrize("empty_instrument", [False, True])
def test_redraft_preserves_tool_selection(handoff, empty_instrument):
    workspace, project, payload, cards = handoff
    original = workspace.save_brief(project, payload | {"instrument": "flash"})
    redraft = payload | {"question": "A revised hypothesis."}
    if empty_instrument:
        redraft["instrument"] = ""  # The built-in drafting tool's default.
    saved = workspace.save_brief(project, redraft)
    assert saved["capability_directory"] == "auto"
    assert saved["instrument"] == original["instrument"] == "flash"


def test_redraft_preserves_direct_descriptor_selection_after_restart(handoff):
    workspace, project, payload, cards = handoff
    workspace.save_brief(project, payload | {"capability_directory": cards[0]["path"]})
    restarted = Workspace(workspace.root)
    assert restarted.save_brief(project, payload)["capability_directory"] == cards[0]["path"]


def test_access_changes_preserve_the_required_solver_until_explicit_clear(handoff):
    workspace, project, payload, cards = handoff
    workspace.save_brief(project, payload | {"instrument": "flash"})
    replaced = workspace.save_brief(project, payload | {"capability_directory": cards[1]["path"]})
    assert replaced["capability_directory"] == cards[1]["path"]
    assert replaced["instrument"] == "flash"
    expanded = workspace.save_brief(project, payload | {"capability_directory": "auto"})
    assert expanded["instrument"] == "flash"
    cleared = workspace.save_brief(project, payload | {"capability_directory": ""})
    assert cleared["capability_directory"] == ""
    assert workspace.save_brief(project, payload)["capability_directory"] == ""


def test_browser_save_of_unchanged_selection_preserves_declared_instrument(handoff):
    workspace, project, payload, cards = handoff
    workspace.save_brief(project, payload | {"instrument": "flash"})
    saved = workspace.save_brief(project, payload | {"capability_directory": "auto"})
    assert saved["instrument"] == "flash"


def test_continuation_selection_is_only_used_without_a_saved_brief(handoff):
    workspace, project, payload, cards = handoff
    path = workspace.directory(project) / "project.json"
    record = load(path)
    record["continuation_draft"] = {"capability_directory": cards[0]["path"]}
    put(path, record)
    assert workspace.save_brief(project, payload)["capability_directory"] == cards[0]["path"]
    workspace.save_brief(project, payload | {"capability_directory": ""})
    assert workspace.save_brief(project, payload)["capability_directory"] == ""


@pytest.mark.parametrize("override", ["", "other"])
def test_launch_rejects_missing_or_wrong_declared_instrument(handoff, monkeypatch, override):
    from conjecture_solver import study_launch

    workspace, project, payload, cards = handoff
    workspace.save_brief(project, payload | {"instrument": "flash"})
    monkeypatch.setattr(workspace, "project_connection", lambda _: {
        "backend": "codex", "model": "fixture",
    })
    monkeypatch.setattr(
        study_launch, "materialize_native",
        lambda _: pytest.fail("Invalid instrument must fail before study materialization"),
    )
    selected = cards[1]["path"] if override else ""
    app = SimjectureWebApplication(runs_root=workspace.root.parent)
    with pytest.raises(ValueError, match="declared instrument"):
        workspace.launch(project, {
            "request_key": "wrong-tool", "capability_directory": selected,
        }, app)
    directory = workspace.directory(project)
    assert not (directory / "studies").exists()
    assert load(directory / "project.json")["studies"] == []


@pytest.mark.parametrize("access", ["auto", "restricted"])
def test_redrafted_tool_reaches_frozen_execution_contract(handoff, monkeypatch, access):
    from conjecture_solver import execution, mvp_launch
    from conjecture_solver.research_service import ResearchService

    workspace, project, payload, cards = handoff
    # This freezes a launch contract without calling a provider or requiring its CLI.
    binary = workspace.root.parent / "bin"
    binary.mkdir()
    codex = binary / "codex"
    codex.write_text("#!/bin/sh\nexit 0\n")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary))
    selection = {"capability_directory": cards[0]["path"]} if access == "restricted" else {}
    workspace.save_brief(project, payload | {"instrument": "flash"} | selection)
    workspace.save_brief(project, payload)
    monkeypatch.setattr(workspace, "project_connection", lambda _: {
        "backend": "codex", "model": "fixture",
    })
    monkeypatch.setattr(execution, "require_execution_backend", lambda _: {"available": True})
    monkeypatch.setattr(
        mvp_launch, "start_managed_campaign", lambda _: SimpleNamespace(close=lambda: None)
    )
    app = SimjectureWebApplication(runs_root=workspace.root.parent)
    result = workspace.launch(project, {"request_key": "correct-tool"}, app)
    root = Path(result["path"])
    manifest = load(root / "research.json")
    expected = {}
    for card in cards[:1] if access == "restricted" else cards:
        installation = MVPCapabilityInstallation.read(card["path"])
        expected[installation.manifest.name] = installation.contract_hash
    assert manifest["capability_hashes"] == expected
    assert "Declared instrument requirement: flash" in manifest["operator_protocol"]
    assert load(root / "study-launch.json")["request"]["capability_directory"]
    assert load(root / "project-brief.json")["instrument"] == "flash"
    service = ResearchService(root)
    (service.work / "calculate.py").write_text("print('ordinary Python')\n")
    binding = service._binding("calculate.py", (), (), None)
    service.check_method(binding, None, "evidence")
    with pytest.raises(ValueError, match="Submit lab.method"):
        service.check_method(service._binding("calculate.py", (), (), "flash-fixture"),
                             None, "evidence")
    service.freeze_requirements({"require_method_review": True})
    with pytest.raises(ValueError, match="Submit lab.method"):
        service.check_method(binding, None, "evidence")


def test_unknown_instrument_does_not_replace_saved_selection(handoff):
    workspace, project, payload, _ = handoff
    workspace.save_brief(project, payload | {"instrument": "flash"})
    path = workspace.directory(project) / "project.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="registered research capability"):
        workspace.save_brief(project, payload | {"instrument": "missing"})
    assert path.read_bytes() == before


def test_automatic_inventory_is_per_study_and_skips_unavailable_tools(handoff):
    from conjecture_solver.mvp_skills import MVPCapabilityRegistry

    workspace, project, payload, cards = handoff
    assert workspace.save_brief(project, payload)["capability_directory"] == "auto"
    first = workspace._available_tools()
    original = MVPCapabilityRegistry.discover(first).hashes
    config = load(cards[1]["path"])
    config["manifest"]["name"] = "third-fixture"
    third = Path(cards[1]["path"]).with_name("third.json")
    put(third, config)
    cards.append(dict(id="third", path=str(third)))
    broken = third.with_name("unavailable.json")
    put(broken, config | {"runtime_root": str(third.parent / "missing-runtime")})
    cards.append(dict(id="unavailable", path=str(broken)))
    second = workspace._available_tools()
    assert first != second
    assert set(MVPCapabilityRegistry.discover(second).hashes) == {*original, "third-fixture"}
    assert MVPCapabilityRegistry.discover(first).hashes == original
    report = load(Path(second).parent / "inventory.json")
    assert any(item["path"] == str(broken) for item in report["skipped"])
    other = MVPCapabilityInstallation.read(cards[1]["path"])
    other.executable.unlink()
    selected = MVPCapabilityRegistry.discover(first, names=("flash-fixture",))
    assert selected.hashes == {"flash-fixture": original["flash-fixture"]}
    with pytest.raises((ValueError, FileNotFoundError)):
        MVPCapabilityRegistry.discover(first, names=("other-fixture",)).get("other-fixture")


@pytest.mark.parametrize("placement", ["local", "remote", "empty"])
def test_native_automatic_access_is_frozen_on_resume(handoff, monkeypatch, placement):
    from conjecture_solver import execution
    from conjecture_solver.execution_pool import MachineRegistry
    from conjecture_solver.study_launch import NativeStudyRequest, materialize_native
    from conjecture_solver.web import inventory

    workspace, _, _, cards = handoff
    remote = placement == "remote"
    pool = dict(
        registry=str(workspace.root / "machines"),
        workers={"remote": {"profile": {"config": {"max_jobs": 1}}}},
        capabilities={"flash--at-remote": {"runtime_sha256": "f" * 64}},
    )
    monkeypatch.setattr(MachineRegistry, "freeze", lambda *_: pool)
    monkeypatch.setattr(execution, "require_execution_backend", lambda _: (
        pytest.fail("A remote-only study must not require a local sandbox") if remote else {}
    ))
    monkeypatch.setattr(inventory, "automatic_tool_paths", lambda: (
        pytest.fail("Remote tools come from the worker pool") if remote
        else [card["path"] for card in cards] if placement == "local" else []
    ))
    root = workspace.root.parent / "native-study"
    request = NativeStudyRequest(
        hypothesis="An automatic tool-access fixture", campaign_id="study",
        output_directory=str(root), agent_executable=sys.executable,
        machine_registry=pool["registry"] if remote else None,
        machine_ids=["remote"] if remote else [],
    )
    materialize_native(request)
    manifest = load(root / "research.json")
    assert set(manifest["capability_hashes"]) == (
        {"flash--at-remote"} if remote else
        {"flash-fixture", "other-fixture"} if placement == "local" else set()
    )
    assert manifest["method_review_scope"] == "used_instruments"
    if placement == "empty":
        from conjecture_solver.research_service import ResearchService

        registry = Path(manifest["capabilities"])
        (registry / "flash.json").symlink_to(cards[0]["path"])
        service = ResearchService(root)
        assert service.register_capability("flash-fixture")["runtime_sha256"]
    before = (root / "research.json").read_bytes()
    monkeypatch.setattr(inventory, "automatic_tool_paths", lambda: pytest.fail("Rediscovery"))
    materialize_native(request, resume=True)
    assert (root / "research.json").read_bytes() == before
