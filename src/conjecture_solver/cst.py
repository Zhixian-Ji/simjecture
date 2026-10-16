"""Operator-side WSL bridge to an existing, licensed Windows CST installation."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from . import cst_windows


def linux_path(value):
    """Accept a WSL path or a Windows absolute path, without invoking a shell."""
    value = os.fspath(value)
    if len(value) > 2 and value[1:3] in (":\\", ":/"):
        return Path(subprocess.check_output(["wslpath", "-u", value], text=True).strip())
    return Path(value).expanduser().resolve()


def windows_path(value):
    return subprocess.check_output(["wslpath", "-w", str(Path(value).resolve())], text=True).strip()


class CSTBridge:
    """Host-authorized execution only; receipts are permanently exploratory."""

    def __init__(self, installation=None, python=None):
        setting = installation or os.environ.get("SIMJECTURE_CST_INSTALLATION")
        if not setting:
            candidates = sorted(Path("/mnt/c/Program Files (x86)").glob("CST Studio Suite *"))
            if len(candidates) != 1:
                raise ValueError("Specify --installation or SIMJECTURE_CST_INSTALLATION")
            setting = candidates[0]
        self.installation = linux_path(setting)
        self.python = (
            linux_path(python) if python else (self.installation / "Opera/code/bin/python.exe")
        )
        if not (self.installation / "AMD64/python_cst_libraries").is_dir():
            raise ValueError("CST Python libraries were not found in this installation")
        if not self.python.is_file():
            raise ValueError("Specify a compatible Windows Python with --python")
        self.runner = Path(cst_windows.__file__).resolve()

    def call(self, action, *args, timeout=60):
        command = [
            str(self.python),
            "-I",
            "-B",
            "-X",
            "utf8",
            windows_path(self.runner),
            action,
            "--installation",
            windows_path(self.installation),
            *args,
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
        if result.returncode:
            raise RuntimeError("CST bridge failed: " + result.stderr.strip()[-4000:])
        # CST's reader can print a notice before the response. The last JSON line
        # is the protocol response; never treat a partial or failed call as success.
        lines = result.stdout.splitlines()
        if not lines:
            raise RuntimeError("CST bridge returned no response")
        payload = json.loads(lines[-1])
        if payload.get("protocol") != cst_windows.PROTOCOL:
            raise ValueError("Unexpected CST bridge protocol")
        return payload

    def check(self):
        return self.call("check")

    def inspect(self, project, results=(), *, allow_open=False):
        args = ["--project", windows_path(linux_path(project))]
        for path in results:
            args.extend(["--result", path])
        if allow_open:
            args.append("--allow-open")
        return self.call("inspect", *args)

    def submit(self, project, job_root, parameters, results, *, timeout=600):
        source = linux_path(project)
        root = linux_path(job_root)
        if source.suffix.lower() != ".cst" or not source.is_file() or source.is_symlink():
            raise ValueError("Supply an existing, self-contained .cst project")
        if root.is_relative_to(source.with_suffix("")):
            raise ValueError("Keep the job root outside the source project's companion directory")
        # CST itself works on a local Windows drive, not a Linux /tmp or UNC path.
        win_root = windows_path(root)
        if len(win_root) < 3 or win_root[1:3] != ":\\":
            raise ValueError("Choose --job-root on a Windows drive mounted in WSL")
        identifier = "cst_" + uuid.uuid4().hex
        job = root / identifier
        request = dict(
            protocol=cst_windows.PROTOCOL,
            installation=windows_path(self.installation),
            python=windows_path(self.python),
            parameters=parameters,
            results=list(results),
            timeout=timeout,
            solver="LF MStatic",
            inputs={"inputs/project.cst": "0" * 64},
            created_at=time.time(),
            runner_sha256=cst_windows.sha(self.runner),
            python_sha256=cst_windows.sha(self.python),
        )
        cst_windows.validate_request(request)
        job.mkdir(parents=True)
        launch_attempted = False
        try:
            inputs = job / "inputs"
            inputs.mkdir()
            shutil.copyfile(source, inputs / "project.cst")
            source_model = source.with_suffix("") / "Model"
            if source_model.is_symlink():
                raise ValueError("CST model inputs must not contain symlinks")
            if source_model.is_dir():
                for path in source_model.rglob("*"):
                    if path.is_symlink():
                        raise ValueError("CST model inputs must not contain symlinks")
                shutil.copytree(source_model, inputs / "project/Model")
            request["inputs"] = {
                path.relative_to(job).as_posix(): cst_windows.sha(path)
                for path in sorted(inputs.rglob("*"))
                if path.is_file()
            }
            if cst_windows.sha(source) != request["inputs"]["inputs/project.cst"]:
                raise ValueError("Source CST project changed during snapshotting; save and retry")
            cst_windows.validate_request(request)
            cst_windows.write(job / "request.json", request)
            cst_windows.write(
                job / "state.json",
                dict(
                    protocol=cst_windows.PROTOCOL,
                    id=identifier,
                    status="queued",
                    created_at=request["created_at"],
                    stage="exploration",
                    evidence_eligible=False,
                    execution_boundary="trusted-windows-process",
                    request_sha256=cst_windows.sha(job / "request.json"),
                ),
            )
            launch_attempted = True
            self.call("submit", "--job", windows_path(job))
        except Exception as error:
            if launch_attempted:
                # The Windows supervisor may have started even if its reply was
                # lost. Do not overwrite its state or claim it has stopped.
                (job / "cancel").touch()
                raise RuntimeError(
                    "CST submission reply was lost; cancellation requested. "
                    "Check the job before retrying: " + str(job)
                ) from error
            cst_windows.write(
                job / "state.json",
                dict(
                    protocol=cst_windows.PROTOCOL,
                    id=identifier,
                    status="failed",
                    error="Submission failed; inspect the request and coordinator error",
                    stage="exploration",
                    evidence_eligible=False,
                ),
            )
            raise
        return status(job)


def status(job):
    job = linux_path(job)
    state = cst_windows.read(job / "state.json")
    state["directory"] = str(job)
    if (
        state["status"] == "succeeded"
        and cst_windows.sha(job / "results.json") != state["results_sha256"]
    ):
        raise ValueError("Recorded CST results changed")
    return state


def cancel(job):
    job = linux_path(job)
    state = status(job)
    if state["status"] not in cst_windows.TERMINAL:
        (job / "cancel").touch()
        state["cancel_requested"] = True
    return state


def wait(job, *, poll_seconds=0.2):
    while True:
        state = status(job)
        if state["status"] in cst_windows.TERMINAL:
            return state
        request = cst_windows.read(linux_path(job) / "request.json")
        if time.time() > request["created_at"] + request["timeout"] + 30:
            raise RuntimeError(
                "Windows supervisor has not reported a terminal state; stop is unconfirmed. "
                "Inspect the Windows processes and supervisor.log."
            )
        time.sleep(poll_seconds)


def parameters(values):
    result = {}
    for value in values:
        name, separator, number = value.partition("=")
        if not separator or name in result:
            raise ValueError("Use distinct --parameter NAME=NUMBER options")
        parsed = float(number)
        if not math.isfinite(parsed):
            raise ValueError("CST parameters must be finite")
        result[name] = parsed
    return result


def configure_parser(parser):
    actions = parser.add_subparsers(dest="cst_action", required=True)
    for name in ("check", "inspect", "submit"):
        action = actions.add_parser(name)
        action.add_argument("--installation")
        action.add_argument("--python")
        if name != "check":
            action.add_argument("--project", required=True)
            action.add_argument("--result", action="append", default=[], required=name == "submit")
        if name == "inspect":
            action.add_argument("--allow-open", action="store_true")
        if name == "submit":
            action.add_argument("--job-root", required=True)
            action.add_argument("--parameter", action="append", default=[])
            action.add_argument("--timeout", type=float, default=600)
        action.set_defaults(handler=main)
    for name in ("status", "cancel", "wait"):
        action = actions.add_parser(name)
        action.add_argument("directory")
        action.set_defaults(handler=main)


def main(args: argparse.Namespace):
    if args.cst_action in ("status", "cancel", "wait"):
        result = globals()[args.cst_action](args.directory)
    else:
        bridge = CSTBridge(args.installation, args.python)
        if args.cst_action == "check":
            result = bridge.check()
        elif args.cst_action == "inspect":
            result = bridge.inspect(args.project, args.result, allow_open=args.allow_open)
        else:
            result = bridge.submit(
                args.project,
                args.job_root,
                parameters(args.parameter),
                args.result,
                timeout=args.timeout,
            )
    print(json.dumps(result, indent=2, allow_nan=False))
    return 1 if result.get("status") in {"failed", "timed_out", "interrupted"} else 0
