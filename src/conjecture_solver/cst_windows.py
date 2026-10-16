"""Standalone Windows CST runner (stdlib only; compatible with CST's Python 3.9).

This is trusted operator-side execution, not a Windows or Linux sandbox.
Do not import CST libraries until after a worker belongs to its Windows Job Object.
"""

from __future__ import annotations

import argparse
import ctypes
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
from contextlib import ExitStack
from pathlib import Path

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
    for relative, expected in request["inputs"].items():
        path = job / relative
        if path.is_symlink() or not path.is_file() or sha(path) != expected:
            raise ValueError("CST input changed: " + relative)
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


class WindowsJob:
    """Closing the native job kills exactly its assigned processes and descendants."""

    def __init__(self):
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("ProcessTime", ctypes.c_longlong),
                ("JobTime", ctypes.c_longlong),
                ("LimitFlags", w.DWORD),
                ("MinWorkingSet", ctypes.c_size_t),
                ("MaxWorkingSet", ctypes.c_size_t),
                ("ActiveProcesses", w.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("Priority", w.DWORD),
                ("Scheduling", w.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOps",
                    "WriteOps",
                    "OtherOps",
                    "ReadBytes",
                    "WriteBytes",
                    "OtherBytes",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("Basic", Basic),
                ("IO", IO),
                ("ProcessMemory", ctypes.c_size_t),
                ("JobMemory", ctypes.c_size_t),
                ("PeakProcessMemory", ctypes.c_size_t),
                ("PeakJobMemory", ctypes.c_size_t),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            w.DWORD,
        ]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.kernel.QueryInformationJobObject.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            w.DWORD,
            ctypes.c_void_p,
        ]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def terminate(self):
        """Confirm assigned Windows processes exit before reporting a stop."""
        from ctypes import wintypes as w

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("UserTime", ctypes.c_longlong),
                ("KernelTime", ctypes.c_longlong),
                ("PeriodUserTime", ctypes.c_longlong),
                ("PeriodKernelTime", ctypes.c_longlong),
                ("PageFaults", w.DWORD),
                ("TotalProcesses", w.DWORD),
                ("ActiveProcesses", w.DWORD),
                ("TerminatedProcesses", w.DWORD),
            ]

        if not self.kernel.TerminateJobObject(self.handle, 125):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            info = Accounting()
            if not self.kernel.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.ActiveProcesses == 0:
                return
            time.sleep(0.05)
        raise TimeoutError("Windows job termination is unconfirmed")


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


def worker(job):
    request = validate_request(read(job / "request.json"))
    while not (job / "assigned").exists():
        if time.time() > request["created_at"] + request["timeout"]:
            raise TimeoutError("Windows Job assignment deadline expired")
        time.sleep(0.05)
    verify_inputs(job, request)
    work = job / "work"
    shutil.copytree(job / "inputs", work)
    project_path = work / "project.cst"
    os.chdir(work)
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
    verify_inputs(job, request)
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
        stage="exploration",
        evidence_eligible=False,
        execution_boundary="trusted-windows-process",
        solver_execution="local",
        parameters=request["parameters"],
        inputs=request["inputs"],
        runtime=read(job / "cst-process.json"),
        runner_sha256=request["runner_sha256"],
        python_sha256=request["python_sha256"],
    )
    write(job / "results.json", data)
    del handles


def stopped(job, request):
    if (job / "cancel").exists():
        return "cancelled"
    if time.time() >= request["created_at"] + request["timeout"]:
        return "timed_out"
    return None


def supervise(job):
    import msvcrt

    request = validate_request(read(job / "request.json"))
    state = read(job / "state.json")
    state.update(supervisor_pid=os.getpid())
    write(job / "state.json", state)
    native_job = None
    child = None
    # One CST job per installation, including when different job roots are used.
    locks = Path(os.environ["LOCALAPPDATA"]) / "Simjecture/cst-locks"
    locks.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(request["installation"].lower().encode()).hexdigest()
    # ExitStack closes the lock only after cleanup and the terminal receipt,
    # including when either execution or final-state writing raises.
    with ExitStack() as resources:
        try:
            lock = resources.enter_context((locks / (key + ".lock")).open("a+b"))
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            while not stopped(job, request):
                try:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                state["status"] = stopped(job, request)
                return
            verify_inputs(job, request)
            native_job = WindowsJob()
            with (job / "worker.log").open("ab") as log:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-u",
                        "-X",
                        "utf8",
                        str(Path(__file__).resolve()),
                        "worker",
                        "--job",
                        str(job),
                    ],
                    stdout=log,
                    stderr=log,
                    cwd=str(job),
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                native_job.assign(child)
                (job / "assigned").touch()
                state.update(status="running", worker_pid=child.pid, started_at=time.time())
                write(job / "state.json", state)
                while child.poll() is None and not stopped(job, request):
                    time.sleep(0.1)
                outcome = stopped(job, request)
                if outcome:
                    native_job.terminate()
                    child.wait(timeout=30)
                    state["status"] = outcome
                elif child.returncode == 0 and (job / "results.json").is_file():
                    state.update(status="succeeded", results_sha256=sha(job / "results.json"))
                else:
                    detail = (job / "worker.log").read_text(encoding="utf-8", errors="replace")
                    state.update(
                        status="failed", return_code=child.returncode, error=detail[-2000:]
                    )
        except Exception as error:
            state.update(status="failed", error=str(error))
            traceback.print_exc()
        finally:
            if native_job:
                try:
                    native_job.terminate()
                except Exception as error:
                    state.update(status="interrupted", error="Stop unconfirmed: " + str(error))
                finally:
                    native_job.close()
            if child and child.poll() is None:
                try:
                    child.kill()
                    child.wait(timeout=30)
                except Exception as error:
                    state.update(status="interrupted", error="Stop unconfirmed: " + str(error))
            state["finished_at"] = time.time()
            write(job / "state.json", state)


def submit(job):
    """Claim a launch once; record a known failure without leaving an ownerless queue."""
    state = read(job / "state.json")
    if state["status"] in TERMINAL:
        return state
    marker = job / "submitted"
    try:
        marker.open("x").close()
    except FileExistsError:
        return read(job / "state.json")
    supervisor = None
    try:
        # Another submitter may have failed and released its marker after our
        # first read. Recheck terminal state only after claiming this launch.
        state = read(job / "state.json")
        if state["status"] in TERMINAL:
            marker.unlink()
            return state
        with (job / "supervisor.log").open("ab") as log:
            supervisor = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-X",
                    "utf8",
                    str(Path(__file__).resolve()),
                    "supervise",
                    "--job",
                    str(job),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                close_fds=True,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
    except Exception as error:
        if supervisor is not None:
            # A failure after Popen returned does not mean the supervisor stopped.
            # Preserve ownership; the coordinator's lost-reply path requests cancel.
            raise
        state.update(
            status="failed",
            error="Failed to start Windows supervisor: " + str(error),
            finished_at=time.time(),
        )
        write(job / "state.json", state)
        marker.unlink()
        return state
    return read(job / "state.json")


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
