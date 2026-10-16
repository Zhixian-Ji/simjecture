"""Shared application protocol, snapshot integrity and adapter contracts."""

import copy
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conjecture_solver import cst, windows
from conjecture_solver import solidworks_windows as solidworks
from conjecture_solver import windows_runtime as runtime
from conjecture_solver.agent_skills import research_skills
from conjecture_solver.cli import build_parser


@pytest.mark.parametrize("code", [5, 32, 33])
def test_receipt_write_retries_windows_sharing_errors_without_losing_old_state(
    tmp_path, monkeypatch, code
):
    path = tmp_path / "state.json"
    runtime.write(path, {"status": "queued"})
    replace = runtime.os.replace
    attempts = []

    def shared(source, destination):
        attempts.append(destination)
        if len(attempts) == 1:
            assert runtime.read(path) == {"status": "queued"}
            error = PermissionError("Windows reader holds the receipt")
            error.winerror = code
            raise error
        return replace(source, destination)

    monkeypatch.setattr(runtime.os, "replace", shared)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    runtime.write(path, {"status": "running"})
    assert len(attempts) == 2
    assert runtime.read(path) == {"status": "running"}
    assert not list(tmp_path.glob("*.tmp"))


def test_receipt_write_preserves_permanent_errors_and_bounds_windows_retries(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    runtime.write(path, {"status": "queued"})
    failure = PermissionError("Persistent access denial")
    attempts = []

    def denied(*_):
        attempts.append(True)
        raise failure

    monkeypatch.setattr(runtime.os, "replace", denied)
    with pytest.raises(PermissionError) as raised:
        runtime.write(path, {"status": "running"})
    assert raised.value is failure and len(attempts) == 1
    failure.winerror = 5
    clock = iter((0, 0, 5))
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError) as raised:
        runtime.write(path, {"status": "running"})
    assert raised.value is failure and len(attempts) == 3
    assert runtime.read(path) == {"status": "queued"}


@pytest.fixture
def sealed_job(tmp_path):
    job = tmp_path / "job"
    (job / "inputs/project/Model").mkdir(parents=True)
    (job / "inputs/project.cst").write_bytes(b"original project")
    (job / "inputs/project/Model/model.dat").write_bytes(b"original model")
    (job / "runtime").mkdir()
    for name in runtime.BUNDLE:
        shutil.copyfile(Path(runtime.__file__).with_name(name), job / "runtime" / name)
    request = dict(
        protocol=runtime.PROTOCOL,
        application="cst",
        operation="magnetostatic.solve",
        installation="C:\\CST",
        python=sys.executable,
        arguments={"parameters": {"r": 2}, "results": ["1D Results\\L"]},
        inputs=runtime.inventory(job / "inputs", "inputs"),
        runtime=runtime.inventory(job / "runtime", "runtime"),
        python_sha256=runtime.sha(sys.executable),
        created_at=time.time(),
        timeout=60,
    )
    runtime.validate_request(request)
    runtime.write(job / "request.json", request)
    runtime.write(
        job / "state.json",
        dict(
            status="queued",
            request_sha256=runtime.sha(job / "request.json"),
        ),
    )
    return job, request


@pytest.mark.parametrize("change", ["added", "removed", "changed", "directory_link", "runtime"])
def test_complete_snapshot_inventory_detects_queued_changes(sealed_job, tmp_path, change):
    job, request = sealed_job
    runtime.verify_inputs(job, request)
    if change == "added":
        (job / "inputs/project/Model/late.dat").write_bytes(b"not in request")
    elif change == "removed":
        (job / "inputs/project/Model/model.dat").unlink()
    elif change == "changed":
        (job / "inputs/project.cst").write_bytes(b"changed")
    elif change == "directory_link":
        outside = tmp_path / "outside"
        outside.mkdir()
        (job / "inputs/project/Model/link").symlink_to(outside, target_is_directory=True)
    else:
        (job / "runtime/unrecorded.py").write_text("raise AssertionError('must not execute')")
    with pytest.raises(ValueError, match="inventory changed|symlinks"):
        runtime.verify_inputs(job, request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("application", "comsol"),
        ("application", []),
        ("operation", "invented.operation"),
        ("timeout", True),
        ("timeout", float("nan")),
        ("arguments", {"parameters": {"r": float("inf")}, "results": ["1D Results\\L"]}),
        ("runtime", {"runtime/windows_runtime.py": "a" * 64}),
        ("inputs", {"inputs/../escape": "a" * 64}),
    ],
)
def test_shared_request_refuses_unsupported_tasks(sealed_job, field, value):
    _, request = sealed_job
    request = copy.deepcopy(request)
    request[field] = value
    with pytest.raises(ValueError):
        runtime.validate_request(request)


def test_worker_checks_working_copy_before_any_application_call(sealed_job, monkeypatch):
    job, _ = sealed_job
    (job / "assigned").touch()
    copytree = runtime.shutil.copytree
    native = runtime.adapter("cst")
    calls = []

    def mixed_copy(source, destination, *args, **kwargs):
        copytree(source, destination, *args, **kwargs)
        if Path(source).name == "inputs":
            (Path(destination) / "project.cst").write_bytes(b"torn working copy")

    monkeypatch.setattr(runtime.shutil, "copytree", mixed_copy)
    monkeypatch.setattr(
        runtime,
        "adapter",
        lambda _: SimpleNamespace(
            validate=native.validate,
            execute=lambda *a: calls.append(a),
        ),
    )
    with pytest.raises(ValueError, match="Working copy differs"):
        runtime.worker(job)
    assert calls == []


def test_changed_adapter_is_refused_before_import(sealed_job, monkeypatch):
    job, _ = sealed_job
    (job / "assigned").touch()
    (job / "runtime/cst_windows.py").write_text("raise AssertionError('must not import')")
    monkeypatch.setattr(runtime, "adapter", lambda _: pytest.fail("modified code must not load"))
    with pytest.raises(ValueError, match="runtime inventory changed"):
        runtime.worker(job)


def test_shared_worker_collects_and_verifies_adapter_artifacts(sealed_job, monkeypatch, tmp_path):
    job, _ = sealed_job
    (job / "assigned").touch()
    native = runtime.adapter("cst")
    monkeypatch.chdir(tmp_path)

    def execute(job, request, context):
        assert request["operation"] == "magnetostatic.solve"
        assert callable(context["claim_process"])
        (job / "work/measurement.csv").write_text("radius,inductance\n2,0.01\n")
        return {"measurement": 0.01}

    monkeypatch.setattr(
        runtime,
        "adapter",
        lambda _: SimpleNamespace(
            validate=native.validate,
            execute=execute,
        ),
    )
    runtime.worker(job)
    result = runtime.read(job / "results.json")
    assert result["application"] == "cst" and result["measurement"] == 0.01
    assert not result["evidence_eligible"] and result["stage"] == "exploration"
    assert (job / "inputs/project.cst").read_bytes() == b"original project"
    state = runtime.read(job / "state.json")
    state.update(status="succeeded", results_sha256=runtime.sha(job / "results.json"))
    runtime.write(job / "state.json", state)
    paths = {item["path"] for item in windows.artifacts(job)["artifacts"]}
    assert "work/measurement.csv" in paths
    (job / "work/measurement.csv").write_text("changed after completion")
    with pytest.raises(ValueError, match="artifact changed"):
        windows.artifacts(job)


def test_offline_discovery_and_cli_do_not_require_vendor_installations(capsys):
    descriptions = runtime.describe()
    products = {item["application"]: item for item in descriptions["applications"]}
    assert set(products) == {"cst", "solidworks"}
    assert "project.python" in products["cst"]["operations"]
    assert "part.rebuild_export" in products["solidworks"]["operations"]
    assert "no native SOLIDWORKS" in products["solidworks"]["qualification"]
    args = build_parser().parse_args(["windows", "describe"])
    assert args.handler(args) == 0
    assert json.loads(capsys.readouterr().out)["protocol"] == runtime.PROTOCOL


def test_common_windows_skill_exposes_the_interface_and_current_adapter_limits():
    catalogue = research_skills()
    assert "windows-applications" in catalogue
    content = catalogue.read("windows-applications", "SKILL.md", max_chars=32000)["content"]
    assert "simjecture windows describe" in content
    assert "dimensions_si" in content and "project.python" in content
    assert "COMSOL is a future adapter" in content
    assert "evidence_eligible=false" in content


@pytest.mark.parametrize("command", ["cst", "windows"])
def test_cancelled_wait_exits_nonzero_but_cancel_request_succeeds(tmp_path, command, capsys):
    runtime.write(tmp_path / "state.json", {"status": "cancelled"})
    args = build_parser().parse_args([command, "wait", str(tmp_path)])
    assert args.handler(args) == 130
    args = build_parser().parse_args([command, "cancel", str(tmp_path)])
    assert args.handler(args) == 0
    assert not (tmp_path / "cancel").exists()
    capsys.readouterr()


def test_frozen_runtime_is_launched_and_contains_all_adapter_files(tmp_path, monkeypatch):
    installation = tmp_path / "CST"
    (installation / "AMD64/python_cst_libraries").mkdir(parents=True)
    python = installation / "python.exe"
    python.write_bytes(b"Windows interpreter")
    source = tmp_path / "project.cst"
    source.write_bytes(b"source")
    monkeypatch.setattr(cst, "windows_path", lambda p: "C:\\" + str(p).lstrip("/"))
    bridge = cst.CSTBridge(installation, python)
    calls = []
    monkeypatch.setattr(bridge, "call", lambda *a, **kw: calls.append((a, kw)))
    receipt = bridge.submit(source, tmp_path / "jobs", {}, ["1D Results\\L"])
    job = Path(receipt["directory"])
    request = runtime.read(job / "request.json")
    assert set(request["runtime"]) == {"runtime/" + name for name in runtime.BUNDLE}
    assert runtime.inventory(job / "runtime", "runtime") == request["runtime"]
    assert calls[0][1]["runner"] == job / "runtime/windows_runtime.py"
    # The standalone bundle can bootstrap both adapters without the Linux package.
    process = subprocess.run(
        [sys.executable, "-I", "-B", str(job / "runtime/windows_runtime.py"), "describe"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(process.stdout)["applications"][0]["application"] == "cst"


def test_new_companion_model_directory_is_detected_during_snapshot(tmp_path, monkeypatch):
    installation = tmp_path / "CST"
    (installation / "AMD64/python_cst_libraries").mkdir(parents=True)
    python = installation / "python.exe"
    python.write_bytes(b"Windows interpreter")
    source = tmp_path / "project.cst"
    source.write_bytes(b"project")
    monkeypatch.setattr(cst, "windows_path", lambda p: "C:\\" + str(p).lstrip("/"))
    bridge = cst.CSTBridge(installation, python)
    copyfile = windows.shutil.copyfile

    def copy_and_create_model(src, dst, *args, **kwargs):
        result = copyfile(src, dst, *args, **kwargs)
        if src == source:
            model = source.with_suffix("") / "Model"
            model.mkdir(parents=True)
            (model / "new.dat").write_bytes(b"created during snapshot")
        return result

    monkeypatch.setattr(windows.shutil, "copyfile", copy_and_create_model)
    monkeypatch.setattr(bridge, "call", lambda *a, **kw: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="changed during snapshotting"):
        bridge.submit(source, tmp_path / "jobs", {}, ["1D Results\\L"])
    job = next((tmp_path / "jobs").iterdir())
    assert runtime.read(job / "state.json")["status"] == "failed"


def test_windows_containment_checks_ignore_path_case_before_copying(tmp_path, monkeypatch):
    installation = tmp_path / "CST"
    (installation / "AMD64/python_cst_libraries").mkdir(parents=True)
    python = installation / "python.exe"
    python.write_bytes(b"Windows interpreter")
    source = tmp_path / "Project.cst"
    source.write_bytes(b"project")
    # These spellings name the same companion directory on Windows.
    root = tmp_path / "PROJECT/Model/jobs"
    monkeypatch.setattr(cst, "windows_path", lambda p: "C:\\" + str(p).lstrip("/"))
    bridge = cst.CSTBridge(installation, python)
    with pytest.raises(ValueError, match="outside.*companion"):
        bridge.submit(source, root, {}, ["1D Results\\L"])
    assert not root.exists()


@pytest.mark.parametrize("bom", [False, True])
def test_cst_native_script_uses_owned_copy_and_saves_without_starting_solver(
    tmp_path,
    monkeypatch,
    bom,
):
    native = runtime.adapter("cst")
    job = tmp_path / "job"
    (job / "work").mkdir(parents=True)
    (job / "inputs").mkdir()
    (job / "work/project.cst").write_bytes(b"working project")
    script = job / "inputs/automation.py"
    source = (
        "import sys\n"
        "assert sys.argv == [__file__]\n"
        "if __name__ == '__main__':\n"
        "    work_directory.joinpath('geometry.txt').write_text('new geometry')\n"
        "    parameters['scratch'] = 5\n"
        "    result = {'edited': True}\n"
    )
    script.write_bytes((b"\xef\xbb\xbf" if bom else b"") + source.encode())
    events = []
    project = SimpleNamespace(
        save=lambda: events.append("save"), close=lambda: events.append("close_project")
    )
    environment = SimpleNamespace(
        pid=lambda: 1,
        version=lambda: "fake CST",
        set_quiet_mode=lambda _: None,
        open_project=lambda _: project,
        close=lambda: events.append("close_application"),
    )
    monkeypatch.setattr(native, "load_cst", lambda _: (None, None, []))
    monkeypatch.setattr(native, "launch_frontend", lambda *a: environment)
    monkeypatch.setattr(native, "inspect_project", lambda *a: {"results": {}, "tree": []})
    request = dict(
        installation="CST",
        operation="project.python",
        arguments={
            "parameters": {},
            "results": [],
            "script": "inputs/automation.py",
        },
    )
    native.validate(
        request["operation"],
        request["arguments"],
        {
            "inputs/project.cst": "a" * 64,
            "inputs/automation.py": "b" * 64,
        },
    )
    result = native.execute(job, request, {})
    assert result["script_result"] == {"edited": True}
    assert request["arguments"]["parameters"] == {}
    assert (job / "work/geometry.txt").read_text() == "new geometry"
    assert events == ["save", "close_project", "close_application"]


@pytest.fixture
def solidworks_case(tmp_path, monkeypatch):
    job = tmp_path / "job"
    (job / "work").mkdir(parents=True)
    (job / "work/project.sldprt").write_bytes(b"part copy")
    events = []
    dimensions = {"D1@Sketch1": SimpleNamespace(SystemValue=0.01)}
    failures = {"claim": False, "rebuild": False, "export": False}

    def export(path, *args):
        events.append("export")
        if failures["export"]:
            return False
        Path(path).write_bytes(b"CAD export")
        return True

    model = SimpleNamespace(
        Parameter=lambda name: dimensions.get(name),
        ForceRebuild3=lambda _: not failures["rebuild"],
        Save3=lambda *a: True,
        GetTitle=lambda: "project.sldprt",
        ClearSelection2=lambda _: events.append("clear_selection"),
        Extension=SimpleNamespace(SaveAs=export),
    )

    def open_document(*args):
        events.append("open")
        return model

    application = SimpleNamespace(
        GetProcessID=lambda: 42,
        SetCurrentWorkingDirectory=lambda _: None,
        OpenDoc6=open_document,
        ActivateDoc3=lambda *a: model,
        CloseDoc=lambda _: events.append("close_document"),
        RevisionNumber=lambda: "fake revision",
        ExitApp=lambda: events.append("exit"),
    )
    pythoncom = SimpleNamespace(
        VT_BYREF=0x4000,
        VT_I4=3,
        CoInitialize=lambda: events.append("initialize"),
        CoUninitialize=lambda: events.append("uninitialize"),
    )
    client = SimpleNamespace(
        DispatchEx=lambda _: application,
        VARIANT=lambda kind, value: SimpleNamespace(value=value),
    )
    monkeypatch.setattr(solidworks, "load_com", lambda: (pythoncom, client))

    def claim(pid, start, executable):
        events.append("claim")
        assert pid == 42 and start > 0
        assert executable.name == "SLDWORKS.exe"
        if failures["claim"]:
            raise RuntimeError("Refusing an existing application session")

    request = dict(
        application="solidworks",
        installation="C:/SOLIDWORKS",
        operation="part.rebuild_export",
        arguments={
            "dimensions_si": {"D1@Sketch1": 0.02},
            "exports": ["step"],
        },
    )
    return SimpleNamespace(
        job=job,
        request=request,
        context={"claim_process": claim, "begin_external_activation": lambda _: time.time()},
        events=events,
        failures=failures,
        dimensions=dimensions,
    )


def test_solidworks_adapter_claims_session_then_edits_rebuilds_and_exports(solidworks_case):
    case = solidworks_case
    solidworks.validate(
        case.request["operation"],
        case.request["arguments"],
        {
            "inputs/project.sldprt": "a" * 64,
        },
    )
    result = solidworks.execute(case.job, case.request, case.context)
    assert result["dimensions_si"] == {"D1@Sketch1": 0.02}
    assert result["exports"] == ["work/project.step"]
    assert case.events == [
        "initialize",
        "claim",
        "open",
        "clear_selection",
        "export",
        "close_document",
        "exit",
        "uninitialize",
    ]


@pytest.mark.parametrize("failure", ["claim", "rebuild", "export"])
def test_solidworks_failure_preserves_unowned_sessions_and_closes_owned_ones(
    solidworks_case,
    failure,
):
    case = solidworks_case
    case.failures[failure] = True
    with pytest.raises(RuntimeError):
        solidworks.execute(case.job, case.request, case.context)
    if failure == "claim":
        assert "open" not in case.events and "exit" not in case.events
    else:
        assert "exit" in case.events
    assert case.events[-1] == "uninitialize"


@pytest.mark.parametrize(
    "arguments",
    [
        {"dimensions_si": {"D1@Sketch1": True}, "exports": ["step"]},
        {"dimensions_si": {"D1@Sketch1": float("nan")}, "exports": ["step"]},
        {"dimensions_si": {}, "exports": ["unknown"]},
        {"dimensions_si": {}, "exports": ["step", "step"]},
    ],
)
def test_solidworks_refuses_ambiguous_dimensions_and_formats(arguments):
    with pytest.raises(ValueError):
        solidworks.validate("part.rebuild_export", arguments, {"inputs/project.sldprt": "a" * 64})
