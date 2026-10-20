"""Minimal study process lifecycle and read-only historical launch contracts.

The launcher materializes operator inputs as files and never builds a shell
string. Hypothesis text stays out of argv except as a path to those files.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel
from .mvp_control import ControlCommand, write_control

OPERATOR_INPUT_DIR = "operator_input"
CONTROLLER_LOG_NAME = "controller.log"
LAUNCH_RECORD_NAME = "launch.json"
SUPERVISOR_RECORD_NAME = "supervisor.json"
HYPOTHESIS_FILE_NAME = "hypothesis.txt"
INSTRUCTION_FILE_NAME = "instruction.txt"
GUIDED_COMMISSIONING_DIR_NAME = "guided_commissioning"
RUN_LOCK_NAME = "runner.lock"
CAMPAIGN_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"
DSH_SESSION_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"
# Historical engine values remain readable in archived launch records.
ReasoningEngine = Literal["native", "dsh"]
REMOVED_WORKFLOW_MESSAGE = (
    "Classical/MVP, DSH, structured and frontier workflows have been removed. "
    "Start a new minimal study with 'simjecture study' or the workspace. "
    "Existing records remain readable; resume older workflows with their original version."
)


class MVPLaunchRequest(StrictModel):
    hypothesis: str = Field(min_length=1)
    instruction: str | None = None
    campaign_id: str = Field(pattern=CAMPAIGN_ID_PATTERN)
    output_directory: str
    max_wall_seconds: float = Field(default=21_600.0, gt=0)
    max_command_seconds: float = Field(default=600.0, gt=0)
    max_workspace_mb: int = Field(default=512, ge=1)
    max_file_mb: int = Field(default=64, ge=1)
    max_memory_mb: int = Field(default=4096, ge=1)
    max_iterations: int | None = Field(default=None, ge=1)
    max_tool_output_chars: int = Field(default=30_000, ge=1)
    command_heartbeat_seconds: float = Field(default=30.0, gt=0)
    literature_search_timeout_seconds: float = Field(default=20.0, gt=0)
    recent_full_turns: int = Field(default=8, ge=1)
    max_model_retries: int = Field(default=3, ge=0)
    model_failover_after: int = Field(default=2, ge=1)
    ledger: str | None = None
    guided_commission: str | None = None
    skills_directory: str | None = None
    capability_directory: str | None = None
    use_glm: bool = False
    reason: str | None = None
    engine: ReasoningEngine = "native"
    dsh_session_id: str | None = Field(default=None, pattern=DSH_SESSION_ID_PATTERN)
    executable: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_route(self) -> MVPLaunchRequest:
        if self.use_glm and not (self.reason and self.reason.strip()):
            raise ValueError("use_glm requires a non-empty reason")
        if self.engine == "native" and self.dsh_session_id is not None:
            raise ValueError("dsh_session_id requires engine='dsh'")
        return self


class MVPLaunchPlan(StrictModel):
    request: MVPLaunchRequest
    output_directory: str
    hypothesis_file: str
    instruction_file: str | None = None
    guided_commission_file: str | None = None
    launch_record: str
    controller_log: str
    argv: tuple[str, ...]
    engine: ReasoningEngine = "native"
    dsh_session_id: str | None = None


class ProcessIdentity(StrictModel):
    pid: int = Field(ge=1)
    starttime: str
    argv: tuple[str, ...]
    launched_at: str
    run_directory: str | None = None


class LaunchConflictError(ValueError):
    """An output directory already contains a different launch contract."""


class RunAlreadyActiveError(RuntimeError):
    """A runner already owns the output-directory lock."""


def default_mvp_executable() -> tuple[str, ...]:
    return (sys.executable, "-m", "conjecture_solver")


def validate_campaign_id(value: str) -> str:
    import re

    candidate = value.strip()
    if not re.fullmatch(CAMPAIGN_ID_PATTERN, candidate):
        raise ValueError(
            "campaign id must start with a letter and use only letters, digits, '_', '.', or '-'"
        )
    return candidate


def materialize_operator_input(
    request: MVPLaunchRequest,
    *,
    create_output: bool = True,
    resume: bool = False,
) -> MVPLaunchPlan:
    from .study_launch import NativeStudyRequest, materialize_native

    if isinstance(request, NativeStudyRequest):
        return materialize_native(request, resume=resume)
    raise ValueError(REMOVED_WORKFLOW_MESSAGE)


def read_process_identity(
    pid: int,
    argv: Sequence[str] = (),
    *,
    run_directory: str | Path | None = None,
) -> ProcessIdentity | None:
    stat_path = Path(f"/proc/{pid}/stat")
    if not stat_path.is_file():
        return None
    try:
        stat_text = stat_path.read_text()
    except OSError:
        return None
    starttime = _starttime_from_stat(stat_text)
    if starttime is None:
        return None
    actual_argv = _read_process_argv(pid)
    # A just-exec'd process can briefly expose an empty cmdline (observed on WSL).
    # Keep this bounded; exited processes still have no valid live identity.
    for _ in range(5):
        if actual_argv is not None or not stat_path.is_file():
            break
        time.sleep(0.01)
        actual_argv = _read_process_argv(pid)
    if actual_argv is None and argv:
        actual_argv = tuple(argv)
    if actual_argv is None:
        return None
    return ProcessIdentity(
        pid=pid,
        starttime=starttime,
        argv=actual_argv,
        launched_at=datetime.now(UTC).isoformat(),
        run_directory=(
            str(Path(run_directory).expanduser().resolve()) if run_directory is not None else None
        ),
    )


def process_identity_matches(expected: ProcessIdentity) -> bool:
    current = read_process_identity(expected.pid)
    if current is None:
        return False
    if current.starttime != expected.starttime or current.argv != expected.argv:
        return False
    state = _state_from_stat_path(expected.pid)
    return state not in {None, "Z", "X"}


def write_supervisor_record(output_directory: str | Path, identity: ProcessIdentity) -> Path:
    root = Path(output_directory).expanduser().resolve()
    operator = _secure_operator_directory(root, create=True)
    path = operator / SUPERVISOR_RECORD_NAME
    bound = identity.model_copy(update={"run_directory": str(root)})
    payload = {
        "schema_version": "0.2.0",
        "pid": bound.pid,
        "starttime": bound.starttime,
        "argv": list(bound.argv),
        "launched_at": bound.launched_at,
        "run_directory": bound.run_directory,
    }
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def load_supervisor_record(output_directory: str | Path) -> ProcessIdentity | None:
    root = Path(output_directory).expanduser().resolve()
    try:
        operator = _secure_operator_directory(root, create=False)
    except (FileNotFoundError, ValueError):
        return None
    path = operator / SUPERVISOR_RECORD_NAME
    payload = _load_secure_json(path, root=root)
    if payload is None or payload.get("schema_version") != "0.2.0":
        return None
    pid = payload.get("pid")
    starttime = payload.get("starttime")
    argv = payload.get("argv")
    launched_at = payload.get("launched_at")
    run_directory = payload.get("run_directory")
    if not isinstance(pid, int) or pid < 1:
        return None
    if not isinstance(starttime, str) or not isinstance(launched_at, str):
        return None
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        return None
    if not isinstance(run_directory, str):
        return None
    try:
        recorded_root = Path(run_directory).expanduser().resolve()
    except OSError:
        return None
    if recorded_root != root or not _argv_targets_run(tuple(argv), root):
        return None
    identity = ProcessIdentity(
        pid=pid,
        starttime=starttime,
        argv=tuple(argv),
        launched_at=launched_at,
        run_directory=str(root),
    )
    return identity if process_identity_matches(identity) else None


class ManagedCampaign:
    """A runner subprocess started by this operator interface."""

    def __init__(
        self,
        plan: MVPLaunchPlan,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
        log_handle: IO[bytes],
    ) -> None:
        self.plan = plan
        self.process = process
        self.identity = identity
        self.log_handle = log_handle

    def poll(self) -> int | None:
        return self.process.poll()

    def is_alive(self) -> bool:
        if self.process.poll() is not None:
            return False
        return process_identity_matches(self.identity)

    def cancel(
        self,
        *,
        wait_interrupt_seconds: float = 20.0,
        wait_terminate_seconds: float = 10.0,
        sleep: Any = time.sleep,
    ) -> str:
        result = request_graceful_cancel(
            self.identity,
            wait_interrupt_seconds=wait_interrupt_seconds,
            wait_terminate_seconds=wait_terminate_seconds,
            sleep=sleep,
        )
        with suppress(Exception):
            self.process.poll()
        return result

    def close(self) -> None:
        with suppress(OSError):
            self.log_handle.close()


def start_managed_campaign(
    plan: MVPLaunchPlan,
    *,
    popen: Any = subprocess.Popen,
) -> ManagedCampaign:
    from .study_launch import NativeStudyRequest

    if not isinstance(plan.request, NativeStudyRequest) or plan.request.mode != "minimal":
        raise ValueError(REMOVED_WORKFLOW_MESSAGE)
    log_path = Path(plan.controller_log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab", buffering=0)
    try:
        process = popen(
            list(plan.argv),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        log_handle.close()
        raise
    identity = read_process_identity(
        process.pid,
        plan.argv,
        run_directory=plan.output_directory,
    )
    if identity is None:
        identity = ProcessIdentity(
            pid=process.pid,
            starttime="unavailable",
            argv=plan.argv,
            launched_at=datetime.now(UTC).isoformat(),
            run_directory=plan.output_directory,
        )
    # Native launchers register themselves after obtaining the study-wide lock.
    # A losing concurrent resume must not overwrite the actual owner's identity.
    if not (Path(plan.output_directory) / "study-launch.json").exists():
        write_supervisor_record(plan.output_directory, identity)
    return ManagedCampaign(
        plan=plan,
        process=process,
        identity=identity,
        log_handle=log_handle,
    )


class ResumeError(ValueError):
    """A campaign cannot be resumed from the stored operator launch record."""


def load_launch_request(run_directory: str | Path) -> MVPLaunchRequest | None:
    root = Path(run_directory).expanduser().resolve()
    try:
        operator = _secure_operator_directory(root, create=False)
    except (FileNotFoundError, ValueError):
        return None
    path = operator / LAUNCH_RECORD_NAME
    payload = _load_secure_json(path, root=root)
    if payload is None:
        return None
    schema_version = payload.get("schema_version")
    if schema_version == "0.1.0":
        raise ResumeError(
            "legacy launch record does not contain the complete MVP contract; "
            "repeat the reviewed original command"
        )
    if schema_version not in {"0.2.0", "0.3.0"}:
        raise ResumeError(f"unsupported launch record schema: {schema_version!r}")
    blockers = payload.get("automatic_resume_blockers") or []
    if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
        raise ResumeError("launch record has invalid automatic_resume_blockers")
    if blockers:
        raise ResumeError(
            "automatic resume is disabled for this non-self-contained contract: "
            + "; ".join(blockers)
            + ". Repeat the reviewed original command instead."
        )
    hypothesis_name = payload.get("hypothesis_file") or HYPOTHESIS_FILE_NAME
    if hypothesis_name != HYPOTHESIS_FILE_NAME:
        raise ResumeError("launch record hypothesis_file must be hypothesis.txt")
    hypothesis_path = _contained_regular_file(
        root,
        operator / HYPOTHESIS_FILE_NAME,
        description="stored hypothesis",
    )
    try:
        hypothesis = hypothesis_path.read_text()
    except OSError:
        raise ResumeError("stored hypothesis could not be read") from None
    expected_hypothesis_hash = payload.get("hypothesis_sha256")
    if expected_hypothesis_hash != _sha256_text(hypothesis):
        raise ResumeError("stored hypothesis identity does not match launch.json")
    instruction = None
    instruction_name = payload.get("instruction_file")
    if instruction_name is not None:
        if instruction_name != INSTRUCTION_FILE_NAME:
            raise ResumeError("launch record instruction_file must be instruction.txt")
        instruction_path = _contained_regular_file(
            root,
            operator / INSTRUCTION_FILE_NAME,
            description="stored instruction",
        )
        instruction = instruction_path.read_text()
        if payload.get("instruction_sha256") != _sha256_text(instruction):
            raise ResumeError("stored instruction identity does not match launch.json")

    guided_commission = None
    guided_name = payload.get("guided_commission")
    if guided_name is not None:
        expected = f"{GUIDED_COMMISSIONING_DIR_NAME}/manifest.json"
        if guided_name != expected:
            raise ResumeError("guided commissioning path is not self-contained")
        guided_path = _contained_regular_file(
            root,
            operator / GUIDED_COMMISSIONING_DIR_NAME / "manifest.json",
            description="guided commissioning manifest",
        )
        from .mvp_guidance import MVPGuidedCommissioningPackage

        package = MVPGuidedCommissioningPackage.read(guided_path)
        if payload.get("guided_commission_sha256") != package.package_sha256:
            raise ResumeError("guided commissioning identity does not match launch.json")
        guided_commission = str(guided_path)

    try:
        request = MVPLaunchRequest(
            hypothesis=hypothesis,
            instruction=instruction,
            campaign_id=str(payload.get("campaign_id") or root.name),
            output_directory=str(root),
            max_wall_seconds=float(payload.get("max_wall_seconds", 21_600.0)),
            max_command_seconds=float(payload.get("max_command_seconds", 600.0)),
            max_workspace_mb=int(payload.get("max_workspace_mb", 512)),
            max_file_mb=int(payload.get("max_file_mb", 64)),
            max_memory_mb=int(payload.get("max_memory_mb", 4096)),
            max_iterations=(
                int(payload["max_iterations"])
                if payload.get("max_iterations") is not None
                else None
            ),
            max_tool_output_chars=int(payload.get("max_tool_output_chars", 30_000)),
            command_heartbeat_seconds=float(payload.get("command_heartbeat_seconds", 30.0)),
            literature_search_timeout_seconds=float(
                payload.get("literature_search_timeout_seconds", 20.0)
            ),
            recent_full_turns=int(payload.get("recent_full_turns", 12)),
            max_model_retries=int(payload.get("max_model_retries", 3)),
            model_failover_after=int(payload.get("model_failover_after", 2)),
            ledger=_load_recorded_path(payload.get("ledger"), root, "ledger"),
            guided_commission=guided_commission,
            skills_directory=_load_recorded_path(
                payload.get("skills_directory"), root, "skills directory"
            ),
            capability_directory=_load_recorded_path(
                payload.get("capability_directory"), root, "capability directory"
            ),
            use_glm=payload.get("use_glm") is True,
            reason=(str(payload["reason"]) if isinstance(payload.get("reason"), str) else None),
            engine=(
                str(payload.get("engine") or "native") if schema_version == "0.3.0" else "native"
            ),
            dsh_session_id=(
                str(payload["dsh_session_id"])
                if schema_version == "0.3.0" and isinstance(payload.get("dsh_session_id"), str)
                else None
            ),
        )
    except ResumeError:
        raise
    except Exception as error:
        raise ResumeError(f"stored launch contract is invalid: {error}") from error
    return request


def request_verified_pause(
    run_directory: str | Path,
    *,
    source: str = "operator",
) -> str:
    """Ask a verified live runner to pause after the current action."""

    if (Path(run_directory) / "study-launch.json").exists():
        from .study_launch import control_native

        return control_native(run_directory, "pause")
    record = load_supervisor_record(run_directory)
    if record is None or not process_identity_matches(record):
        return "no verified running process; nothing to pause"
    write_control(run_directory, ControlCommand.PAUSE, source=source)
    return "pause requested at the next action boundary"


def prepare_resume(run_directory: str | Path) -> MVPLaunchPlan:
    root = Path(run_directory).expanduser().resolve()
    if (root / "study-launch.json").exists():
        from .study_launch import resume_native

        return resume_native(root)
    if not root.is_dir():
        raise ResumeError(f"run directory does not exist: {root}")
    raise ResumeError(REMOVED_WORKFLOW_MESSAGE)

def request_graceful_cancel(
    identity: ProcessIdentity,
    *,
    wait_interrupt_seconds: float = 20.0,
    wait_terminate_seconds: float = 10.0,
    sleep: Any = time.sleep,
) -> str:
    """Cancel a verified campaign tree, then its supervisor process.

    Detached kernel workers are deliberately restart-safe, so killing only the
    campaign supervisor can otherwise leave a simulation consuming CPU/GPU.
    When ``run_directory`` identifies a campaign, verified non-terminal jobs
    are cancelled before the supervisor and reconciled once more after it
    exits. Never signals an unverified PID. Does not use SIGKILL or SIGSTOP.
    """

    if not process_identity_matches(identity):
        return "process identity does not match; no signal sent"
    _cancel_active_campaign_jobs(
        identity,
        wait_interrupt_seconds=wait_interrupt_seconds,
        wait_terminate_seconds=wait_terminate_seconds,
        sleep=sleep,
    )
    if not _signal_verified(identity, signal.SIGINT):
        result = "process already exited"
    elif _wait_for_exit(identity, wait_interrupt_seconds, sleep=sleep):
        result = "interrupted"
    elif not process_identity_matches(identity):
        result = "process identity changed after interrupt; no further signal sent"
    elif not _signal_verified(identity, signal.SIGTERM):
        result = "interrupted"
    elif _wait_for_exit(identity, wait_terminate_seconds, sleep=sleep):
        result = "terminated"
    else:
        result = "cancel requested; process still running"
    # Close the small race in which a worker handshake became durable between
    # the first job scan and the supervisor signal.
    _cancel_active_campaign_jobs(
        identity,
        wait_interrupt_seconds=wait_interrupt_seconds,
        wait_terminate_seconds=wait_terminate_seconds,
        sleep=sleep,
    )
    return result


def _cancel_active_campaign_jobs(
    identity: ProcessIdentity,
    *,
    wait_interrupt_seconds: float,
    wait_terminate_seconds: float,
    sleep: Any,
) -> tuple[str, ...]:
    """Cancel verified durable jobs below ``identity.run_directory``.

    This is intentionally a best-effort companion to supervisor cancellation:
    malformed or absent job state must never prevent signalling the already
    verified campaign PID. Individual job cancellation still performs its own
    PID/start-time/argv verification immediately before every signal.
    """

    if identity.run_directory is None:
        return ()
    try:
        root = Path(identity.run_directory).expanduser().resolve()
    except OSError:
        return ()
    if (root / "research.json").exists():
        from .research_service import ResearchService

        ResearchService(root).cancel_active()
    jobs_root = root / "jobs"
    if not (jobs_root / "jobs").is_dir():
        return ()
    try:
        # Local import avoids the module-level cycle: campaign_jobs reuses this
        # verified signal primitive for each detached worker.
        from .campaign_jobs import CampaignJobSupervisor

        supervisor = CampaignJobSupervisor(jobs_root)
        outcomes: list[str] = []
        for state in supervisor.jobs():
            if state.status.terminal:
                continue
            cancelled = supervisor.cancel(
                state.job_id,
                wait_interrupt_seconds=min(max(0.0, wait_interrupt_seconds), 5.0),
                wait_terminate_seconds=min(max(0.0, wait_terminate_seconds), 2.0),
                sleep=sleep,
            )
            outcomes.append(f"{state.operation_id}:{cancelled.status.value}")
        return tuple(outcomes)
    except Exception:  # noqa: BLE001 - parent cancellation must still proceed
        return ()


def _signal_verified(identity: ProcessIdentity, sig: signal.Signals) -> bool:
    if not process_identity_matches(identity):
        return False
    try:
        group = os.getpgid(identity.pid)
        if group == identity.pid:
            os.killpg(group, sig)
        else:
            os.kill(identity.pid, sig)
    except ProcessLookupError:
        return False
    return True


def _wait_for_exit(
    identity: ProcessIdentity,
    timeout_seconds: float,
    *,
    sleep: Any,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        if not process_identity_matches(identity):
            return True
        sleep(0.05)
    return not process_identity_matches(identity)


class MVPOutputLock:
    """Non-blocking exclusive ownership of one durable run directory."""

    def __init__(self, run_directory: str | Path) -> None:
        self.root = Path(run_directory).expanduser().resolve()
        self._fd: int | None = None

    def __enter__(self) -> MVPOutputLock:
        self.root.mkdir(parents=True, exist_ok=True)
        operator = _secure_operator_directory(self.root, create=True)
        path = operator / RUN_LOCK_NAME
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as error:
            raise RunAlreadyActiveError(
                f"cannot safely open the run lock: {path}: {error}"
            ) from error
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(fd)
            raise RunAlreadyActiveError(
                f"another runner owns the output directory: {self.root}"
            ) from error
        self._fd = fd
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()}\n".encode())
        os.fsync(fd)
        return self

    def __exit__(self, *_args: object) -> None:
        if self._fd is None:
            return
        with suppress(OSError):
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        with suppress(OSError):
            os.close(self._fd)
        self._fd = None


def output_lock_is_held(run_directory: str | Path) -> bool:
    """Return true when another process owns the durable runner lock."""

    root = Path(run_directory).expanduser().resolve()
    try:
        operator = _secure_operator_directory(root, create=False)
    except FileNotFoundError:
        return False
    except ValueError:
        return True
    path = operator / RUN_LOCK_NAME
    if path.is_symlink():
        return True
    if not path.exists():
        return False
    flags = os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError:
        return True
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _secure_operator_directory(root: Path, *, create: bool) -> Path:
    if not root.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {root}")
    operator = root / OPERATOR_INPUT_DIR
    if operator.is_symlink():
        raise ValueError("operator_input must not be a symlink")
    if not operator.exists():
        if not create:
            raise FileNotFoundError(operator)
        operator.mkdir(mode=0o700)
    if not operator.is_dir() or operator.resolve() != operator:
        raise ValueError("operator_input must be a contained regular directory")
    return operator


def _contained_regular_file(root: Path, path: Path, *, description: str) -> Path:
    if path.is_symlink():
        raise ResumeError(f"{description} must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, FileNotFoundError) as error:
        raise ResumeError(f"{description} is missing or unreadable") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ResumeError(f"{description} must be a regular file inside the run")
    return resolved


def _load_secure_json(path: Path, *, root: Path) -> dict[str, Any] | None:
    if not path.exists() or path.is_symlink():
        return None
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            return None
        payload = json.loads(resolved.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_recorded_path(value: Any, root: Path, description: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ResumeError(f"stored {description} path is invalid")
    candidate = Path(value)
    resolved = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ResumeError(f"stored {description} path escapes the run directory")
    return str(resolved)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _read_process_argv(pid: int) -> tuple[str, ...] | None:
    path = Path(f"/proc/{pid}/cmdline")
    try:
        encoded = path.read_bytes()
    except OSError:
        return None
    parts = encoded.rstrip(b"\0").split(b"\0") if encoded else []
    if not parts:
        return None
    return tuple(part.decode(errors="surrogateescape") for part in parts)


def _argv_targets_run(argv: tuple[str, ...], root: Path) -> bool:
    native_modules = {
        "conjecture_solver.study",
        "conjecture_solver.agent_supervisor",
        "conjecture_solver.research_supervisor",
    }
    native_aliases = {"simjecture-supervise", "simjecture-research"}
    module = argv[2] if len(argv) > 2 and argv[1] == "-m" else None
    native = module in native_modules or any(Path(item).name in native_aliases for item in argv[:2])
    if not native:
        command = next((item for item in ("study", "mvp", "dsh-run") if item in argv), None)
        if command is None:
            return False
        prefix = argv[: argv.index(command)]
        module_entry = module == "conjecture_solver"
        console_entry = any(
            Path(item).name in {"acs", "conjecture-solver", "simjecture"} for item in prefix[:2]
        )
        if not module_entry and not console_entry:
            return False
        native = command == "study"
    flag = "--campaign" if native else "--output"
    output_value = None
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            output_value = argv[index + 1]
            break
        if item.startswith(flag + "="):
            output_value = item.split("=", 1)[1]
            break
    if not output_value:
        return False
    try:
        return Path(output_value).expanduser().resolve() == root
    except OSError:
        return False


def _stat_fields(stat_text: str) -> list[str] | None:
    close = stat_text.rfind(")")
    if close < 0:
        return None
    return stat_text[close + 2 :].split()


def _starttime_from_stat(stat_text: str) -> str | None:
    fields = _stat_fields(stat_text)
    if fields is None or len(fields) < 20:
        return None
    return fields[19]


def _state_from_stat_path(pid: int) -> str | None:
    path = Path(f"/proc/{pid}/stat")
    if not path.is_file():
        return None
    try:
        fields = _stat_fields(path.read_text())
    except OSError:
        return None
    if not fields:
        return None
    return fields[0]


def _atomic_write_bytes(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode())
