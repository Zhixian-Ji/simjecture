"""CST bridge contracts run without Windows, CST, a license, or model calls."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conjecture_solver import cst
from conjecture_solver import cst_windows as runner
from conjecture_solver.agent_skills import research_skills
from conjecture_solver.cli import build_parser


@pytest.fixture
def cst_request():
    return dict(
        protocol=runner.PROTOCOL,
        installation=r"C:\CST",
        python=r"C:\Python\python.exe",
        parameters={"r": 2.0},
        results=[r"1D Results\Ms Solver\Inductance Matrix\L"],
        timeout=60,
        solver="LF MStatic",
        inputs={"inputs/project.cst": "a" * 64},
        created_at=1234,
        runner_sha256="b" * 64,
        python_sha256="c" * 64,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("parameters", {'r"\nDeleteResults': 1}),
        ("parameters", {"r": '1: Shell("command")'}),
        ("parameters", {"r": float("nan")}),
        ("parameters", {"r": True}),
        ("timeout", -1),
        ("timeout", float("inf")),
        ("timeout", True),
        ("solver", "PARAMETERSWEEP"),
        ("results", [r"1D Results\x", r"1D Results\x"]),
        ("results", []),
        ("results", ["2D/3D Results\\field"]),
        ("inputs", {"inputs/../../escape": "a" * 64}),
        ("inputs", {r"inputs\..\escape": "a" * 64}),
        ("inputs", {"inputs/x": "bad hash"}),
    ],
)
def test_boundary_rejects_unsafe_or_ambiguous_requests(cst_request, field, value):
    cst_request[field] = value
    with pytest.raises(ValueError):
        runner.validate_request(cst_request)


def test_request_accepts_numeric_parameters_and_explicit_paths(cst_request):
    assert runner.validate_request(cst_request) == cst_request
    cst_request["extra"] = "never silently ignored"
    with pytest.raises(ValueError):
        runner.validate_request(cst_request)


@pytest.mark.parametrize("values", [["r=nan"], ["r=2", "r=3"], ["r"], ["r=1+2"]])
def test_cli_rejects_ambiguous_parameters(values):
    with pytest.raises(ValueError):
        cst.parameters(values)


def test_complex_results_keep_both_parts_and_reject_nonfinite():
    assert runner.encode_numbers([1 + 2j]) == [{"real": 1.0, "imag": 2.0}]
    with pytest.raises(ValueError, match="non-finite"):
        runner.encode_numbers([float("inf")])


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    installation = tmp_path / "CST"
    (installation / "AMD64/python_cst_libraries").mkdir(parents=True)
    python = installation / "python.exe"
    python.write_bytes(b"test Windows Python identity")
    monkeypatch.setattr(cst, "windows_path", lambda p: "C:\\" + str(p).lstrip("/"))
    return cst.CSTBridge(installation, python)


def test_submit_freezes_model_but_excludes_prior_results(bridge, tmp_path, monkeypatch):
    source = tmp_path / "Two Coils.cst"
    source.write_bytes(b"operator project")
    model = source.with_suffix("") / "Model"
    model.mkdir(parents=True)
    (model / "parameters.json").write_text('{"r":2}')
    old = source.with_suffix("") / "Result"
    old.mkdir()
    (old / "old-result.stx").write_text("not a fresh observation")
    calls = []
    monkeypatch.setattr(bridge, "call", lambda *a: calls.append(a))
    state = bridge.submit(source, tmp_path / "jobs", {"r": 1.5}, [r"1D Results\L"])
    job = Path(state["directory"])
    cst_request = runner.read(job / "request.json")
    assert cst_request["parameters"] == {"r": 1.5}
    assert len(cst_request["inputs"]) == 2
    assert all("Result/" not in p for p in cst_request["inputs"])
    assert not (job / "work").exists()  # The Windows worker creates a separate writable copy.
    assert source.read_bytes() == b"operator project"
    assert (old / "old-result.stx").read_text() == "not a fresh observation"
    assert not state["evidence_eligible"] and state["stage"] == "exploration"
    assert calls[0][0] == "submit"


def test_model_symlink_is_rejected_before_windows_submission(bridge, tmp_path, monkeypatch):
    source = tmp_path / "project.cst"
    source.write_bytes(b"project")
    model = source.with_suffix("") / "Model"
    model.mkdir(parents=True)
    (model / "outside").symlink_to(tmp_path / "secret")
    monkeypatch.setattr(bridge, "call", lambda *a: pytest.fail("must not launch Windows"))
    with pytest.raises(ValueError, match="symlinks"):
        bridge.submit(source, tmp_path / "jobs", {}, [r"1D Results\L"])


def test_linux_job_root_is_not_silently_used_as_windows_workspace(bridge, tmp_path, monkeypatch):
    source = tmp_path / "project.cst"
    source.write_bytes(b"project")
    monkeypatch.setattr(cst, "windows_path", lambda p: r"\\wsl.localhost\distro\tmp\job")
    with pytest.raises(ValueError, match="Windows drive"):
        bridge.submit(source, tmp_path / "jobs", {}, [r"1D Results\L"])
    assert not (tmp_path / "jobs").exists()


def test_lost_submit_reply_requests_cancel_without_falsifying_running_state(
    bridge, tmp_path, monkeypatch
):
    source = tmp_path / "project.cst"
    source.write_bytes(b"project")
    root = tmp_path / "jobs"

    def lost(*args):
        job = next(root.iterdir())
        state = runner.read(job / "state.json")
        state["status"] = "running"
        runner.write(job / "state.json", state)
        raise TimeoutError("lost response after launch")

    monkeypatch.setattr(bridge, "call", lost)
    with pytest.raises(RuntimeError, match="reply was lost"):
        bridge.submit(source, root, {}, [r"1D Results\L"])
    job = next(root.iterdir())
    assert runner.read(job / "state.json")["status"] == "running"
    assert (job / "cancel").exists()


def test_windows_transport_uses_argv_and_rejects_failed_or_wrong_protocol(bridge, monkeypatch):
    observed = {}

    def run(argv, **kwargs):
        observed.update(argv=argv, **kwargs)
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"protocol": runner.PROTOCOL}), stderr=""
        )

    monkeypatch.setattr(cst.subprocess, "run", run)
    bridge.inspect("project.cst", [r"1D Results\a & b"])
    assert "shell" not in observed
    assert r"1D Results\a & b" in observed["argv"]
    monkeypatch.setattr(
        cst.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=1, stdout=json.dumps({"protocol": runner.PROTOCOL}), stderr="solver error"
        ),
    )
    with pytest.raises(RuntimeError, match="solver error"):
        bridge.check()


def test_status_detects_modified_output_and_cancel_preserves_terminal_job(tmp_path):
    result = tmp_path / "results.json"
    result.write_text('{"measurement":1}')
    runner.write(
        tmp_path / "state.json", dict(status="succeeded", results_sha256=runner.sha(result))
    )
    assert cst.cancel(tmp_path)["status"] == "succeeded"
    assert not (tmp_path / "cancel").exists()
    result.write_text('{"measurement":2}')
    with pytest.raises(ValueError, match="changed"):
        cst.status(tmp_path)


def test_cancellation_is_a_request_until_supervisor_confirms(tmp_path):
    runner.write(tmp_path / "state.json", {"status": "running"})
    state = cst.cancel(tmp_path)
    assert state["status"] == "running" and state["cancel_requested"]
    assert (tmp_path / "cancel").is_file()
    assert runner.read(tmp_path / "state.json")["status"] == "running"


def test_deadline_includes_queueing_and_cancel_is_terminal_choice(
    tmp_path, cst_request, monkeypatch
):
    monkeypatch.setattr(runner.time, "time", lambda: cst_request["created_at"] + 61)
    assert runner.stopped(tmp_path, cst_request) == "timed_out"
    (tmp_path / "cancel").touch()
    assert runner.stopped(tmp_path, cst_request) == "cancelled"


def test_inputs_and_runner_are_checked_before_execution(tmp_path, cst_request):
    (tmp_path / "inputs").mkdir()
    p = tmp_path / "inputs/project.cst"
    p.write_bytes(b"sealed")
    cst_request = copy.deepcopy(cst_request)
    cst_request["inputs"]["inputs/project.cst"] = hashlib.sha256(b"sealed").hexdigest()
    p.write_bytes(b"modified")
    with pytest.raises(ValueError, match="input changed"):
        runner.verify_inputs(tmp_path, cst_request)


def test_cst_cli_and_usage_skill_are_discoverable():
    parser = build_parser()
    assert parser.parse_args(["cst", "check"]).cst_action == "check"
    assert parser.parse_args(["cst", "cancel", "/jobs/cst_test"]).directory == "/jobs/cst_test"
    assert "cst" in research_skills()
    content = research_skills().read("cst", "SKILL.md", max_chars=32000)["content"]
    assert "exploration" in content and "Windows" in content


def test_job_root_cannot_recursively_copy_the_source_model(bridge, tmp_path):
    source = tmp_path / "project.cst"
    source.write_bytes(b"model")
    root = source.with_suffix("") / "Model/jobs"
    with pytest.raises(ValueError, match="outside"):
        bridge.submit(source, root, {}, [r"1D Results\L"])
    assert not root.exists()


def test_wait_does_not_claim_a_stop_when_windows_supervisor_is_lost(tmp_path, monkeypatch):
    runner.write(tmp_path / "state.json", {"status": "running"})
    runner.write(tmp_path / "request.json", {"created_at": 1, "timeout": 1})
    monkeypatch.setattr(cst.time, "time", lambda: 100)
    with pytest.raises(RuntimeError, match="unconfirmed"):
        cst.wait(tmp_path)
    assert runner.read(tmp_path / "state.json")["status"] == "running"


def test_elevation_error_is_actionable_and_never_retries_with_runas(monkeypatch, tmp_path):
    calls = []

    def blocked(*args, **kwargs):
        calls.append(args)
        error = OSError("Elevation required")
        error.winerror = 740
        raise error

    monkeypatch.setattr(runner.subprocess, "Popen", blocked)
    with pytest.raises(RuntimeError, match="WinError 740"):
        runner.launch_frontend("C:/CST", tmp_path / "project.cst", None)
    assert len(calls) == 1
    assert str(tmp_path / "project.cst") not in calls[0][0]


@pytest.mark.parametrize("observed_r", [2.0, 1.0])
def test_worker_uses_copy_and_rejects_results_from_other_parameters(
    tmp_path, cst_request, monkeypatch, observed_r
):
    (tmp_path / "inputs").mkdir()
    source = tmp_path / "inputs/project.cst"
    source.write_bytes(b"preserve input")
    runner.write(tmp_path / "request.json", cst_request)
    (tmp_path / "assigned").touch()
    commands = []
    project = SimpleNamespace(
        schematic=SimpleNamespace(execute_vba_code=commands.append),
        save=lambda: None,
        close=lambda: None,
    )
    environment = SimpleNamespace(
        pid=lambda: 123,
        version=lambda: "test CST fixture",
        set_quiet_mode=lambda _: None,
        open_project=lambda _: project,
        close=lambda: None,
    )
    monkeypatch.setattr(runner, "load_cst", lambda _: (None, None, []))
    monkeypatch.setattr(runner, "launch_frontend", lambda *a: environment)
    monkeypatch.setattr(runner, "verify_inputs", lambda *a: None)
    monkeypatch.setattr(runner.os, "chdir", lambda _: None)
    monkeypatch.setattr(
        runner,
        "inspect_project",
        lambda *a: {
            "results": {"test L": [{"parameters": {"r": observed_r}, "y": 0.001}]},
        },
    )
    if observed_r != 2:
        with pytest.raises(ValueError, match="parameters differ"):
            runner.worker(tmp_path)
        assert not (tmp_path / "results.json").exists()
    else:
        runner.worker(tmp_path)
        output = runner.read(tmp_path / "results.json")
        assert output["stage"] == "exploration" and not output["evidence_eligible"]
    assert source.read_bytes() == b"preserve input"
    assert (tmp_path / "work/project.cst").read_bytes() == b"preserve input"
    assert "DeleteResults" in commands[0]
    assert "MStaticSolver.Start <> 0" in commands[1]
