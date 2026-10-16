"""Operator-side WSL bridge to an existing, licensed Windows CST installation."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from pathlib import Path

from . import cst_windows, windows, windows_runtime
from .windows import WindowsBridge


def linux_path(value):
    """Accept a WSL path or a Windows absolute path, without invoking a shell."""
    value = os.fspath(value)
    if len(value) > 2 and value[1:3] in (":\\", ":/"):
        return Path(subprocess.check_output(["wslpath", "-u", value], text=True).strip())
    return Path(value).expanduser().resolve()


def windows_path(value):
    return subprocess.check_output(["wslpath", "-w", str(Path(value).resolve())], text=True).strip()


def source_manifest(source):
    """Hash the complete source inventory; callers must keep the project quiescent."""
    files = {"inputs/project.cst": cst_windows.sha(source)}
    model = source.with_suffix("") / "Model"
    if model.is_symlink():
        raise ValueError("CST model inputs must not contain symlinks")
    if model.exists() and not model.is_dir():
        raise ValueError("CST Model must be a directory")
    for path in sorted(model.rglob("*")):
        if path.is_symlink():
            raise ValueError("CST model inputs must not contain symlinks")
        if path.is_file():
            relative = "inputs/project/Model/" + path.relative_to(model).as_posix()
            files[relative] = cst_windows.sha(path)
        elif not path.is_dir():
            raise ValueError("CST model inputs must be regular files or directories")
    return files


class CSTBridge(WindowsBridge):
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
        self.application = "cst"
        self.runner = Path(windows_runtime.__file__).resolve()

    def _windows_path(self, value):
        return windows_path(value)

    def call(self, action, *args, timeout=60, runner=None):
        return super().call(action, *args, timeout=timeout, runner=runner)

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
        source, root = linux_path(project), linux_path(job_root)
        if root.is_relative_to(source.with_suffix("")):
            raise ValueError("Keep the job root outside the source project's companion directory")
        return self.submit_application(
            source, root, "magnetostatic.solve",
            {"parameters": parameters, "results": list(results)}, timeout=timeout,
        )


def status(job):
    return windows.status(job)


def cancel(job):
    return windows.cancel(job)


def wait(job, *, poll_seconds=0.2):
    return windows.wait(job, poll_seconds=poll_seconds)


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
    return windows.exit_code(args.cst_action, result)
