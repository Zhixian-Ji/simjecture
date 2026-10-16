"""Exercise the native CST supervisor boundary without CST, a license or dependencies.

Run with Windows Python 3.9+; the ordinary Linux tests cover snapshot and VBA policy.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

RUNNER = None


def process_alive(pid):
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = w.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def worker_fixture(job, hold):
    """Spawn a descendant only after native Job Object assignment, then fail or wait."""
    deadline = time.monotonic() + 10
    while not (job / "assigned").exists():
        if time.monotonic() > deadline:
            return 2
        time.sleep(0.01)
    child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"])
    (job / "descendant.pid").write_text(str(child.pid))
    while hold:
        time.sleep(0.1)
    return 1


class NativeSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="simjecture-cst-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.job = self.root / "job"
        self.job.mkdir()
        self.profile = patch.dict(os.environ, LOCALAPPDATA=str(self.root / "profile"))
        self.profile.start()
        self.addCleanup(self.profile.stop)
        self.installation = "C:\\CST-Test-" + self.root.name
        self.request = dict(
            protocol=RUNNER.PROTOCOL,
            installation=self.installation,
            python=sys.executable,
            parameters={"r": 2.0},
            results=[r"1D Results\L"],
            timeout=30,
            solver="LF MStatic",
            inputs={"inputs/project.cst": "a" * 64},
            created_at=time.time(),
            runner_sha256="b" * 64,
            python_sha256="c" * 64,
        )
        RUNNER.write(self.job / "request.json", self.request)
        RUNNER.write(self.job / "state.json", {"protocol": RUNNER.PROTOCOL, "status": "queued"})
        key = hashlib.sha256(self.installation.lower().encode()).hexdigest()
        self.lock = self.root / "profile/Simjecture/cst-locks" / (key + ".lock")

    def lock_available(self):
        import msvcrt

        with self.lock.open("a+b") as handle:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return True

    def worker_command(self, hold=False):
        command = [
            sys.executable,
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "--worker-job",
            str(self.job),
        ]
        if hold:
            command.append("--hold-worker")
        return command

    def test_lock_covers_descendant_cleanup_and_terminal_receipt(self):
        real_popen, real_terminate, real_write = (
            subprocess.Popen,
            RUNNER.WindowsJob.terminate,
            RUNNER.write,
        )
        observations = []
        unrelated = real_popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"])

        def launch(*args, **kwargs):
            return real_popen(self.worker_command(), **kwargs)

        def terminate(native):
            pid = int((self.job / "descendant.pid").read_text())
            observations.append((process_alive(pid), self.lock_available()))
            return real_terminate(native)

        def write(path, value):
            if path == self.job / "state.json" and "finished_at" in value:
                self.assertFalse(self.lock_available(), "Lock released before receipt")
            return real_write(path, value)

        try:
            with (
                patch.object(RUNNER, "verify_inputs"),
                patch.object(subprocess, "Popen", launch),
                patch.object(RUNNER.WindowsJob, "terminate", terminate),
                patch.object(RUNNER, "write", write),
            ):
                RUNNER.supervise(self.job)
            self.assertEqual(RUNNER.read(self.job / "state.json")["status"], "failed")
            self.assertEqual(observations, [(True, False)])
            self.assertFalse(process_alive(int((self.job / "descendant.pid").read_text())))
            self.assertTrue(process_alive(unrelated.pid))
            self.assertTrue(self.lock_available())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=10)

    def stop_active_worker(self, outcome):
        real_popen, real_stopped = subprocess.Popen, RUNNER.stopped
        now = [self.request["created_at"] + 0.1]
        deadline = time.monotonic() + 10

        def launch(*args, **kwargs):
            return real_popen(self.worker_command(hold=True), **kwargs)

        def stopped(job, request):
            if (job / "descendant.pid").exists():
                if outcome == "cancelled":
                    (job / "cancel").touch()
                else:
                    now[0] = request["created_at"] + request["timeout"] + 1
            # Bound this fixture even if a child fails before writing its PID.
            if time.monotonic() > deadline:
                now[0] = request["created_at"] + request["timeout"] + 1
            return real_stopped(job, request)

        with (
            patch.object(RUNNER, "verify_inputs"),
            patch.object(subprocess, "Popen", launch),
            patch.object(RUNNER, "stopped", stopped),
            patch.object(time, "time", lambda: now[0]),
        ):
            RUNNER.supervise(self.job)
        self.assertEqual(RUNNER.read(self.job / "state.json")["status"], outcome)
        self.assertFalse(process_alive(int((self.job / "descendant.pid").read_text())))
        self.assertTrue(self.lock_available())

    def test_cancel_terminates_assigned_descendant(self):
        self.stop_active_worker("cancelled")

    def test_deadline_terminates_assigned_descendant(self):
        self.stop_active_worker("timed_out")

    def test_supervisor_creation_failure_is_terminal(self):
        with patch.object(
            subprocess, "Popen", side_effect=OSError("CreateProcess failed")
        ) as launch:
            state = RUNNER.submit(self.job)
            self.assertEqual(state["status"], "failed")
            self.assertIn("CreateProcess failed", state["error"])
            self.assertFalse((self.job / "submitted").exists())
            self.assertEqual(RUNNER.submit(self.job), state)
            self.assertEqual(launch.call_count, 1)

    def test_duplicate_submission_starts_only_once(self):
        with patch.object(subprocess, "Popen", return_value=SimpleNamespace(pid=42)) as launch:
            self.assertEqual(RUNNER.submit(self.job)["status"], "queued")
            self.assertEqual(RUNNER.submit(self.job)["status"], "queued")
            self.assertEqual(launch.call_count, 1)


def main():
    global RUNNER

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runner",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src/conjecture_solver/cst_windows.py",
    )
    parser.add_argument("--worker-job", type=Path)
    parser.add_argument("--hold-worker", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Run this check with Windows Python 3.9+")
    if args.worker_job:
        return worker_fixture(args.worker_job, args.hold_worker)
    spec = importlib.util.spec_from_file_location("cst_windows_runner", args.runner)
    RUNNER = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(RUNNER)
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(NativeSupervisorTests)
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
