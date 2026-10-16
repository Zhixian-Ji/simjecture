"""Standalone Windows CST runner (stdlib only; compatible with CST's Python 3.9).

This is trusted operator-side execution, not a Windows or Linux sandbox.
Do not import CST libraries until after a worker belongs to its Windows Job Object.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

# A standalone Windows invocation uses -I, which excludes the script directory.
# Load the fixed sibling by path instead of importing the Linux package/dependencies.
if __package__:
    from . import windows_runtime as shared
else:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "simjecture_windows_runtime",
        Path(__file__).with_name("windows_runtime.py"),
    )
    shared = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(shared)


TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
PROTOCOL = "simjecture-cst/1"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_request(value):
    """Validate on both sides of the WSL/Windows boundary; accept no VBA expressions."""
    allowed = {
        "protocol",
        "installation",
        "python",
        "parameters",
        "results",
        "timeout",
        "solver",
        "inputs",
        "created_at",
        "runner_sha256",
        "python_sha256",
    }
    if not isinstance(value, dict) or set(value) != allowed:
        raise ValueError("Invalid CST request fields")
    if value["protocol"] != PROTOCOL or value["solver"] != "LF MStatic":
        raise ValueError("This bridge supports LF MStatic projects with protocol " + PROTOCOL)
    for field in ("timeout", "created_at"):
        number = value[field]
        if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
            raise ValueError(field + " must be positive and finite")
    if value["timeout"] > 86400:
        raise ValueError("CST timeout exceeds one day")
    if not isinstance(value["parameters"], dict) or len(value["parameters"]) > 100:
        raise ValueError("Invalid CST parameters")
    for name, number in value["parameters"].items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
            raise ValueError("Invalid CST parameter name")
        if type(number) not in (float, int) or not math.isfinite(number):
            raise ValueError("CST parameter values must be finite numbers")
    paths = value["results"]
    if not isinstance(paths, list) or not 1 <= len(paths) <= 100:
        raise ValueError("Declare between 1 and 100 CST result tree paths")
    for name in paths:
        if (
            not isinstance(name, str)
            or len(name) > 1024
            or "\x00" in name
            or not name.startswith(("1D Results\\", "Tables\\"))
        ):
            raise ValueError("Use an explicit CST 0D/1D result tree path")
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate CST result path")
    if not isinstance(value["inputs"], dict) or not value["inputs"]:
        raise ValueError("CST inputs must be snapshotted")
    for name, digest in value["inputs"].items():
        p = Path(name)
        if (
            not name
            or "\\" in name
            or p.is_absolute()
            or ".." in p.parts
            or not name.startswith("inputs/")
            or ":" in name
        ):
            raise ValueError("Invalid CST snapshot path")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid CST input hash")
    for name in ("runner_sha256", "python_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", value[name]):
            raise ValueError("Invalid CST runtime hash")
    return value


def verify_inputs(job, request):
    if shared.inventory(job / "inputs", "inputs") != request["inputs"]:
        raise ValueError("CST input changed: complete inventory differs")
    if sha(job / "request.json") != read(job / "state.json")["request_sha256"]:
        raise ValueError("CST request changed since submission")
    if sha(Path(__file__)) != request["runner_sha256"]:
        raise ValueError("CST runner changed since submission")
    if sha(request["python"]) != request["python_sha256"]:
        raise ValueError("Windows Python changed since submission")


def load_cst(installation):
    if sys.platform != "win32":
        raise RuntimeError("The CST runner must execute using Windows Python")
    root = Path(installation)
    handles = [os.add_dll_directory(str(root / "AMD64"))]
    opera = root / "Opera/code/bin/Library/bin"
    if opera.is_dir():
        handles.append(os.add_dll_directory(str(opera)))
    sys.path.insert(0, str(root / "AMD64/python_cst_libraries"))
    import cst.interface
    import cst.results

    return cst.interface, cst.results, handles


def inspect_project(installation, project, paths, allow_open=False):
    _, results, handles = load_cst(installation)
    file = results.ProjectFile(project, allow_interactive=allow_open)
    module = file.get_3d()
    tree = module.get_tree_items()
    data = {"protocol": PROTOCOL, "tree": tree, "results": {}}
    for path in paths:
        if path not in tree:
            raise ValueError("CST result not found: " + path)
        rows = []
        for run in module.get_run_ids(path):
            item = module.get_result_item(path, run)
            rows.append(
                {
                    "run_id": run,
                    "parameters": module.get_parameter_combination(run),
                    "x": item.get_xdata(),
                    "y": encode_numbers(item.get_ydata()),
                    "xlabel": item.xlabel,
                    "ylabel": item.ylabel,
                }
            )
        if not rows:
            raise ValueError("No saved runs for CST result: " + path)
        data["results"][path] = rows
    del handles
    return data


def encode_numbers(value):
    if isinstance(value, complex):
        return {"real": encode_numbers(value.real), "imag": encode_numbers(value.imag)}
    if isinstance(value, (list, tuple)):
        return [encode_numbers(x) for x in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("CST produced non-finite result data")
    return value


WindowsJob = shared.WindowsJob


def launch_frontend(installation, project, interface):
    executable = Path(installation) / "AMD64/CST DESIGN ENVIRONMENT_AMD64.exe"
    try:
        # Open the model only after connecting to this exact new PID. Passing
        # its path to a launcher could forward it to an existing user session.
        frontend = subprocess.Popen([str(executable), "-s"], cwd=str(project.parent))
    except OSError as error:
        if getattr(error, "winerror", None) == 740:
            raise RuntimeError(
                "CST requires Windows elevation (WinError 740). Use an authorized Windows "
                "launch context compatible with this installation. The bridge does not "
                "change Run as administrator settings or request UAC elevation."
            ) from error
        raise
    print("Launched CST PID " + str(frontend.pid), flush=True)
    while frontend.poll() is None:
        if frontend.pid in interface.running_design_environments():
            return interface.DesignEnvironment.connect(frontend.pid)
        time.sleep(0.2)
    raise RuntimeError("CST frontend exited before its automation interface became ready")


def _execute_cst(job, request):
    project_path = job / "work/project.cst"
    print("Loading CST automation libraries", flush=True)
    interface, _, handles = load_cst(request["installation"])
    print("Starting a new CST frontend", flush=True)
    environment = launch_frontend(request["installation"], project_path, interface)
    print("Connected to CST PID " + str(environment.pid()), flush=True)
    environment.set_quiet_mode(True)
    write(job / "cst-process.json", {"pid": environment.pid(), "version": environment.version()})
    try:
        print("Opening the working project copy", flush=True)
        project = environment.open_project(str(project_path))
        script_result = None
        if request.get("operation") == "project.python":
            script = job / request["arguments"]["script"]
            namespace = {
                "project": project,
                "environment": environment,
                "work_directory": project_path.parent,
                "parameters": dict(request["parameters"]),
                "result": {},
                "__name__": "__main__",
                "__file__": str(script),
            }
            previous_argv = sys.argv
            try:
                sys.argv = [str(script)]
                exec(compile(script.read_bytes(), str(script), "exec"), namespace)
            finally:
                sys.argv = previous_argv
            script_result = namespace["result"]
            if not isinstance(script_result, dict):
                raise ValueError("A native script must leave result as a JSON object")
            project.save()
            project.close()
        else:
            commands = ["Sub Main()", "DeleteResults", 'ChangeSolverType "LF MStatic"']
            for name in request["parameters"]:
                commands.append(
                    'If Not DoesParameterExist("' + name + '") Then '
                    'Err.Raise 1001, "Simjecture", "Unknown parameter: ' + name + '"'
                )
            for name, value in request["parameters"].items():
                commands.append('StoreParameter "' + name + '", "' + repr(value) + '"')
            commands.extend(
                [
                    'If Not Rebuild() Then Err.Raise 1002, "Simjecture", "Model rebuild failed"',
                    "End Sub",
                ]
            )
            print("Deleting copied results, updating parameters and rebuilding", flush=True)
            project.schematic.execute_vba_code("\n".join(commands))
            project.save()
            print("Starting magnetostatic solver", flush=True)
            # Invoke this solver explicitly. A saved example may select a parameter
            # sweep as its active top-level task; that must not replay other cases.
            # Rebuild can restore distributed settings from history. Override them
            # here, immediately before Start: remote/DC services are outside our job.
            project.schematic.execute_vba_code(
                "Sub Main()\nMStaticSolver.UseDistributedComputing False\n"
                "If MStaticSolver.Start <> 0 Then "
                'Err.Raise 1003, "Simjecture", "Magnetostatic solver failed"\nEnd Sub'
            )
            print("Saving fresh results", flush=True)
            project.save()
            project.close()
    finally:
        print("Closing the job's CST frontend", flush=True)
        environment.close()
    data = inspect_project(request["installation"], str(project_path), request["results"])
    for rows in data["results"].values():
        for row in rows:
            for name, expected in request["parameters"].items():
                actual = row["parameters"].get(name)
                if type(actual) not in (float, int) or not math.isclose(
                    actual, expected, rel_tol=1e-12, abs_tol=0
                ):
                    raise ValueError("Result parameters differ from the submitted job: " + name)
    data.update(
        parameters=request["parameters"],
        cst_runtime=read(job / "cst-process.json"),
    )
    if script_result is not None:
        data["script_result"] = script_result
    del handles
    return data


def worker(job):
    """Compatibility entrypoint for the original CST request protocol."""
    request = validate_request(read(job / "request.json"))
    while not (job / "assigned").exists():
        if time.time() > request["created_at"] + request["timeout"]:
            raise TimeoutError("Windows Job assignment deadline expired")
        time.sleep(0.05)
    verify_inputs(job, request)
    shutil.copytree(job / "inputs", job / "work")
    if shared.inventory(job / "work", "inputs") != shared.inventory(job / "inputs", "inputs"):
        raise ValueError("Working copy differs from the CST input snapshot")
    os.chdir(job / "work")
    data = _execute_cst(job, request)
    verify_inputs(job, request)
    data["runtime"] = data.pop("cst_runtime")
    data.update(
        stage="exploration",
        evidence_eligible=False,
        execution_boundary="trusted-windows-process",
        solver_execution="local",
        inputs=request["inputs"],
        runner_sha256=request["runner_sha256"],
        python_sha256=request["python_sha256"],
    )
    write(job / "results.json", data)


stopped = shared.stopped


def supervise(job):
    return shared.supervise(job, _compat=_compatibility_hooks())


def submit(job):
    return shared.submit(job, _compat=_compatibility_hooks())


def _compatibility_hooks():
    from types import SimpleNamespace

    return SimpleNamespace(
        read=read,
        write=write,
        validate_request=validate_request,
        verify_inputs=verify_inputs,
        WindowsJob=WindowsJob,
        entrypoint=Path(__file__).resolve(),
        stopped=stopped,
    )


def describe():
    parameters = {
        "type": "object",
        "maxProperties": 100,
        "propertyNames": {"pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$"},
        "additionalProperties": {"type": "number"},
    }
    results = {
        "type": "array",
        "minItems": 1,
        "maxItems": 100,
        "uniqueItems": True,
        "items": {"type": "string", "maxLength": 1024, "pattern": r"^(1D Results|Tables)\\"},
    }
    return {
        "application": "cst",
        "project_suffixes": [".cst"],
        "automation": "vendor Python API and VBA",
        "session": "owned desktop process",
        "operations": {
            "magnetostatic.solve": {
                "arguments_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["parameters", "results"],
                    "properties": {"parameters": parameters, "results": results},
                },
                "arguments": {
                    "parameters": "finite numeric values in model units",
                    "results": "explicit CST 0D/1D tree paths",
                },
            },
            "project.python": {
                "arguments_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["parameters", "results", "script"],
                    "properties": {
                        "parameters": parameters,
                        "results": dict(results, minItems=0),
                        "script": {"const": "inputs/automation.py"},
                    },
                },
                "arguments": {
                    "parameters": "finite numeric model parameters",
                    "results": "optional saved 0D/1D paths",
                    "script": "inputs/automation.py",
                },
                "trusted_host_code": True,
            },
        },
        "requirements": ["CST installation with Python automation libraries", "Windows Python"],
        "qualification": (
            "Original magnetostatic recipe tested with CST 2024.4; "
            "shared runner and script operation require native commissioning"
        ),
    }


def check(installation):
    interface, _, handles = load_cst(installation)
    result = {
        "interfaces_loaded": True,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "installation": installation,
        "automation_available": hasattr(interface, "DesignEnvironment"),
        "solver_tested": False,
    }
    del handles
    return result


def input_sources(project, script=None):
    project = Path(project)
    if project.suffix.lower() != ".cst" or not project.is_file():
        raise ValueError("Supply an existing, self-contained .cst project")
    sources = {"inputs/project.cst": project}
    model = project.with_suffix("") / "Model"
    if model.exists() or model.is_symlink():
        sources["inputs/project/Model"] = model
    if script is not None:
        sources["inputs/automation.py"] = Path(script)
    return sources


def validate_job_root(project, root):
    if root.is_relative_to(project.with_suffix("")):
        raise ValueError("Keep the job root outside the source project's companion directory")


def validate(operation, arguments, inputs):
    if operation not in describe()["operations"] or not isinstance(arguments, dict):
        raise ValueError("Unsupported CST operation or arguments")
    fields = {"parameters", "results"}
    if operation == "project.python":
        fields.add("script")
        if (
            arguments.get("script") != "inputs/automation.py"
            or "inputs/automation.py" not in inputs
        ):
            raise ValueError("Stage a native Python script as inputs/automation.py")
    if set(arguments) != fields:
        raise ValueError("Invalid CST operation arguments")
    results = arguments["results"]
    if operation == "project.python" and results == []:
        results = ["1D Results\\validation-placeholder"]
    # Reuse the existing native CST parameter/result language validation.
    validate_request(
        dict(
            protocol=PROTOCOL,
            installation="CST",
            python="python.exe",
            parameters=arguments["parameters"],
            results=results,
            timeout=1,
            created_at=1,
            solver="LF MStatic",
            inputs=inputs,
            runner_sha256="0" * 64,
            python_sha256="0" * 64,
        )
    )
    if "inputs/project.cst" not in inputs:
        raise ValueError("Missing CST project snapshot")
    for name in inputs:
        if name == "inputs/project.cst" or name.startswith("inputs/project/Model/"):
            continue
        if operation == "project.python" and name == "inputs/automation.py":
            continue
        raise ValueError("Unexpected CST input: " + name)


def execute(job, request, context):
    del context
    arguments = request["arguments"]
    native = dict(request, parameters=arguments["parameters"], results=arguments["results"])
    return _execute_cst(job, native)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "inspect", "submit", "supervise", "worker"))
    parser.add_argument("--installation")
    parser.add_argument("--job", type=Path)
    parser.add_argument("--project")
    parser.add_argument("--result", action="append", default=[])
    parser.add_argument("--allow-open", action="store_true")
    args = parser.parse_args()
    if args.action == "check":
        interface, _, handles = load_cst(args.installation)
        print(
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "interfaces_loaded": True,
                    "python": sys.version.split()[0],
                    "platform": sys.platform,
                    "installation": args.installation,
                    "automation_available": hasattr(interface, "DesignEnvironment"),
                    "solver_tested": False,
                }
            )
        )
        del handles
    elif args.action == "inspect":
        result = inspect_project(args.installation, args.project, args.result, args.allow_open)
        print(json.dumps(result, allow_nan=False))
    elif args.action == "submit":
        print(json.dumps(submit(args.job)))
    elif args.action == "supervise":
        supervise(args.job)
    else:
        worker(args.job)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
