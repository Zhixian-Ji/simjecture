"""CST bridge contracts run without Windows, CST, a license, or model calls."""

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conjecture_solver import cst
from conjecture_solver import cst_windows as runner
from conjecture_solver import windows as windows_client
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
    monkeypatch.setattr(bridge, "call", lambda *a, **kw: calls.append((a, kw)))
    state = bridge.submit(source, tmp_path / "jobs", {"r": 1.5}, [r"1D Results\L"])
    job = Path(state["directory"])
    cst_request = runner.read(job / "request.json")
    assert cst_request["arguments"]["parameters"] == {"r": 1.5}
    assert cst_request["application"] == "cst"
    assert cst_request["operation"] == "magnetostatic.solve"
    assert len(cst_request["inputs"]) == 2
    assert all("Result/" not in p for p in cst_request["inputs"])
    assert not (job / "work").exists()  # The Windows worker creates a separate writable copy.
    assert source.read_bytes() == b"operator project"
    assert (old / "old-result.stx").read_text() == "not a fresh observation"
    assert not state["evidence_eligible"] and state["stage"] == "exploration"
    assert calls[0][0][0] == "submit"
    assert calls[0][1]["runner"] == job / "runtime/windows_runtime.py"


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

    def lost(*args, **kwargs):
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
            returncode=0,
            stdout=json.dumps({"protocol": windows_client.runtime.PROTOCOL, "application": "cst"}),
            stderr="",
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
    monkeypatch.setattr(windows_client.time, "time", lambda: 100)
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
@pytest.mark.parametrize("local_setting_error", [False, True])
def test_worker_uses_copy_and_rejects_results_from_other_parameters(
    tmp_path, cst_request, monkeypatch, observed_r, local_setting_error
):
    (tmp_path / "inputs").mkdir()
    source = tmp_path / "inputs/project.cst"
    source.write_bytes(b"preserve input")
    runner.write(tmp_path / "request.json", cst_request)
    (tmp_path / "assigned").touch()
    commands = []
    solver = {"distributed": True, "started": False, "closed": False}

    def execute(code):
        commands.append(code)
        for line in code.splitlines():
            if "Rebuild()" in line:
                solver["distributed"] = True  # History restores the project's DC setting.
            if "UseDistributedComputing False" in line:
                if local_setting_error:
                    raise RuntimeError("Local execution setting failed")
                solver["distributed"] = False
            if "MStaticSolver.Start" in line:
                assert not solver["distributed"], "A distributed solve escaped the Windows job"
                solver["started"] = True

    project = SimpleNamespace(
        schematic=SimpleNamespace(execute_vba_code=execute),
        save=lambda: None,
        close=lambda: None,
    )
    environment = SimpleNamespace(
        pid=lambda: 123,
        version=lambda: "test CST fixture",
        set_quiet_mode=lambda _: None,
        open_project=lambda _: project,
        close=lambda: solver.update(closed=True),
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
    if local_setting_error:
        with pytest.raises(RuntimeError, match="Local execution setting failed"):
            runner.worker(tmp_path)
        assert not solver["started"]
        assert not (tmp_path / "results.json").exists()
    elif observed_r != 2:
        with pytest.raises(ValueError, match="parameters differ"):
            runner.worker(tmp_path)
        assert not (tmp_path / "results.json").exists()
    else:
        runner.worker(tmp_path)
        output = runner.read(tmp_path / "results.json")
        assert output["stage"] == "exploration" and not output["evidence_eligible"]
        assert output["solver_execution"] == "local"
    assert source.read_bytes() == b"preserve input"
    assert (tmp_path / "work/project.cst").read_bytes() == b"preserve input"
    assert "DeleteResults" in commands[0]
    assert "MStaticSolver.Start <> 0" in commands[1]
    assert solver["closed"]


@pytest.mark.parametrize("change", ["mixed", "restored", "added", "removed", "container"])
def test_source_changes_are_rejected_before_launch(bridge, tmp_path, monkeypatch, change):
    source = tmp_path / "project.cst"
    source.write_bytes(b"saved project")
    model = source.with_suffix("") / "Model"
    model.mkdir(parents=True)
    a, b = model / "a", model / "b"
    a.write_text("0")
    b.write_text("0")
    copytree = windows_client.shutil.copytree

    def changing_copy(src, dst):
        if change in {"mixed", "restored"}:
            # Enforce the interleaving: the copied (0, 1) never existed in source.
            dst.mkdir(parents=True)
            windows_client.shutil.copyfile(a, dst / "a")
            a.write_text("1")
            b.write_text("1")
            windows_client.shutil.copyfile(b, dst / "b")
            if change == "restored":
                a.write_text("0")
                b.write_text("0")
        else:
            copytree(src, dst)
            if change == "added":
                (model / "new").write_text("new input")
            elif change == "removed":
                b.unlink()
            else:
                source.write_bytes(b"new project")

    monkeypatch.setattr(windows_client.shutil, "copytree", changing_copy)
    monkeypatch.setattr(bridge, "call", lambda *a: pytest.fail("must not launch Windows"))
    with pytest.raises(ValueError, match="changed during snapshotting"):
        bridge.submit(source, tmp_path / "jobs", {"r": 2}, [r"1D Results\L"])
    job = next((tmp_path / "jobs").iterdir())
    assert runner.read(job / "state.json")["status"] == "failed"
    assert not (job / "submitted").exists()


@pytest.fixture
def windows_process_flags(monkeypatch):
    monkeypatch.setattr(runner.subprocess, "CREATE_NEW_PROCESS_GROUP", 0, raising=False)
    monkeypatch.setattr(runner.subprocess, "DETACHED_PROCESS", 0, raising=False)


@pytest.fixture
def supervisor_case(tmp_path, cst_request, monkeypatch, windows_process_flags):
    """Exercise real supervisor control flow without importing Windows/CST libraries."""
    job = tmp_path / "job"
    job.mkdir()
    runner.write(job / "request.json", cst_request)
    runner.write(job / "state.json", {"protocol": runner.PROTOCOL, "status": "queued"})
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=1, locking=lambda *a: None))
    now = [cst_request["created_at"] + 1]
    monkeypatch.setattr(runner.time, "time", lambda: now[0])
    original_open, original_write = Path.open, runner.write
    locks, events = [], []

    def tracked_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path.suffix == ".lock":
            locks.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", tracked_open)

    def held():
        assert locks and not locks[-1].closed, "Installation lock released before cleanup"

    def run(outcome, *, cleanup_error=False, write_error=False):
        assigned = [False]

        class Child:
            pid = 42
            returncode = 0 if outcome == "success" else 1

            def poll(self):
                if self.returncode is None:
                    if outcome == "cancel":
                        (job / "cancel").touch()
                    elif outcome == "timeout":
                        now[0] = cst_request["created_at"] + cst_request["timeout"] + 1
                return self.returncode

            def kill(self):
                held()
                events.append("kill")
                self.returncode = -9

            def wait(self, timeout):
                held()
                return self.returncode

        child = Child()
        if outcome in {"cancel", "timeout", "assign_error"}:
            child.returncode = None

        class NativeJob:
            def assign(self, process):
                held()
                if outcome == "assign_error":
                    raise OSError("Assignment failed")
                assigned[0] = True

            def terminate(self):
                held()
                events.append("terminate")
                if cleanup_error:
                    raise OSError("Cannot confirm native termination")
                if assigned[0] and child.returncode is None:
                    child.returncode = -15

            def close(self):
                held()
                events.append("close")

        def launch(*args, **kwargs):
            if outcome == "spawn_error":
                raise OSError("Worker creation failed")
            return child

        def verify(*args):
            if outcome == "verify_error":
                raise ValueError("Input mismatch")

        def write(path, value):
            if path == job / "state.json" and "finished_at" in value:
                held()
                events.append("terminal")
                if write_error:
                    raise OSError("Receipt drive unavailable")
            return original_write(path, value)

        if outcome == "success":
            original_write(job / "results.json", {"fresh": True})
        monkeypatch.setattr(runner, "WindowsJob", NativeJob)
        monkeypatch.setattr(runner.subprocess, "Popen", launch)
        monkeypatch.setattr(runner, "verify_inputs", verify)
        monkeypatch.setattr(runner, "write", write)
        runner.supervise(job)
        return runner.read(job / "state.json")

    return SimpleNamespace(run=run, locks=locks, events=events)


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("success", "succeeded"),
        ("failure", "failed"),
        ("cancel", "cancelled"),
        ("timeout", "timed_out"),
        ("assign_error", "failed"),
        ("spawn_error", "failed"),
        ("verify_error", "failed"),
    ],
)
def test_supervisor_holds_lock_through_cleanup_and_receipt(supervisor_case, outcome, expected):
    state = supervisor_case.run(outcome)
    assert state["status"] == expected
    assert supervisor_case.events[-1] == "terminal"
    assert all(handle.closed for handle in supervisor_case.locks)


def test_cleanup_failure_is_unconfirmed_under_the_lock(supervisor_case):
    state = supervisor_case.run("failure", cleanup_error=True)
    assert state["status"] == "interrupted" and "Stop unconfirmed" in state["error"]
    assert supervisor_case.events == ["terminate", "close", "terminal"]
    assert all(handle.closed for handle in supervisor_case.locks)


def test_receipt_write_failure_still_releases_installation_lock(supervisor_case):
    with pytest.raises(OSError, match="Receipt drive unavailable"):
        supervisor_case.run("failure", write_error=True)
    assert supervisor_case.events == ["terminate", "close", "terminal"]
    assert all(handle.closed for handle in supervisor_case.locks)


@pytest.mark.parametrize("failure", ["process", "log"])
def test_supervisor_creation_failure_records_a_terminal_receipt(
    tmp_path, monkeypatch, windows_process_flags, failure
):
    runner.write(tmp_path / "state.json", {"protocol": runner.PROTOCOL, "status": "queued"})
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise OSError("CreateProcess failed")

    monkeypatch.setattr(runner.subprocess, "Popen", fail)
    if failure == "log":
        original_open = Path.open

        def open_log(path, *args, **kwargs):
            if path.name == "supervisor.log":
                raise OSError("CreateProcess failed: log unavailable")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_log)
    state = runner.submit(tmp_path)
    assert state["status"] == "failed" and "CreateProcess failed" in state["error"]
    assert "finished_at" in state and "supervisor_pid" not in state
    assert runner.read(tmp_path / "state.json") == state
    assert not (tmp_path / "submitted").exists()
    # A terminal failed job is immutable; an operator retry gets a new job ID.
    assert runner.submit(tmp_path) == state
    assert len(attempts) == (1 if failure == "process" else 0)


def test_launch_claim_rechecks_a_concurrently_failed_receipt(
    tmp_path, monkeypatch, windows_process_flags
):
    failed = {"protocol": runner.PROTOCOL, "status": "failed", "error": "Other launch failed"}
    runner.write(tmp_path / "state.json", failed)
    original_read = runner.read
    reads = []

    def stale_first_read(path):
        reads.append(path)
        if len(reads) == 1:
            return {"protocol": runner.PROTOCOL, "status": "queued"}
        return original_read(path)

    monkeypatch.setattr(runner, "read", stale_first_read)
    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not relaunch")
    )
    assert runner.submit(tmp_path) == failed
    assert not (tmp_path / "submitted").exists()


def test_duplicate_submit_does_not_start_another_supervisor(
    tmp_path, monkeypatch, windows_process_flags
):
    runner.write(tmp_path / "state.json", {"protocol": runner.PROTOCOL, "status": "queued"})
    launches = []

    def launch(*args, **kwargs):
        launches.append(args)
        return SimpleNamespace(pid=42)

    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    assert runner.submit(tmp_path)["status"] == "queued"
    assert runner.submit(tmp_path)["status"] == "queued"
    assert len(launches) == 1 and (tmp_path / "submitted").exists()


def test_failure_after_supervisor_started_preserves_ownership(
    tmp_path, monkeypatch, windows_process_flags
):
    runner.write(tmp_path / "state.json", {"protocol": runner.PROTOCOL, "status": "queued"})
    original_open = Path.open

    class BrokenLogClose:
        def __enter__(self):
            self.handle = original_open(tmp_path / "supervisor.log", "ab")
            return self.handle

        def __exit__(self, *args):
            self.handle.close()
            raise OSError("Log close failed after launch")

    def open_log(path, *args, **kwargs):
        if path.name == "supervisor.log":
            return BrokenLogClose()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_log)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(pid=42))
    with pytest.raises(OSError, match="after launch"):
        runner.submit(tmp_path)
    assert (tmp_path / "submitted").exists()
    assert runner.read(tmp_path / "state.json")["status"] == "queued"
