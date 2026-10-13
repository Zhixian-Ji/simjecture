"""Budget-bound instrument probes and recorded director tuning."""

from __future__ import annotations

import json
import sys
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field


class RegistrationTuning(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_attempts: int = Field(strict=True, gt=0, le=sys.maxsize)
    reason: str = Field(min_length=16, max_length=2400)


class RegistrationService:
    def registration_policy(self):
        path = self.root / "registration_policy.json"
        return json.loads(path.read_text()) if path.exists() else self.manifest.get(
            "registration_policy", {"director_can_tune": False}
        )

    def configure_registration_policy(self, *, director_can_tune, reason):
        """Operator delegation; absent from the worker RPC API."""
        from .research_service import put

        if type(director_can_tune) is not bool or not isinstance(reason, str) or not reason.strip():
            raise ValueError("Require explicit registration authority and an operator reason")
        with self.lock():
            path = self.root / "registration_policy.json"
            if path.exists():
                if self.registration_policy()["director_can_tune"] != director_can_tune:
                    raise ValueError("Registration delegation is immutable within a study")
                return self.registration_policy()
            policy = dict(
                director_can_tune=director_can_tune, reason=reason, created_at=time.time()
            )
            put(path, policy)
            return policy

    def registration_limits(self):
        limits = dict(timeout_seconds=60.0, max_attempts=1)
        for index, record in enumerate(
            sorted(self._all("registration_adjustments"), key=lambda r: r["sequence"]), start=1
        ):
            if record["sequence"] != index or record["before"] != limits:
                raise ValueError("Registration tuning history is inconsistent")
            request = RegistrationTuning.model_validate(record["request"]).model_dump()
            limits = {k: request[k] for k in limits}
            if record["after"] != limits:
                raise ValueError("Registration tuning history is inconsistent")
        return limits

    def tune_registration(self, request, *, director_decision):
        """Host action following a director decision; no worker self-grants."""
        from .research_service import fingerprint, put

        request = RegistrationTuning.model_validate(request).model_dump()
        if not isinstance(director_decision, str) or not director_decision.startswith("director_"):
            raise ValueError("Require the originating director decision")
        with self.lock():
            if not self.registration_policy()["director_can_tune"]:
                raise ValueError("Operator has not delegated registration tuning to the director")
            identifier = "registration_" + fingerprint(director_decision)[:24]
            path = self.root / "registration_adjustments" / (identifier + ".json")
            if path.exists():
                record = self._read("registration_adjustments", identifier)
                if record["request"] != request:
                    raise ValueError(
                        "Director decision already granted different registration limits"
                    )
                return record
            if time.time() >= self.registration_deadline():
                raise ValueError("Registration compute budget exhausted")
            before = self.registration_limits()
            record = dict(
                id=identifier, sequence=len(self._all("registration_adjustments")) + 1,
                created_at=time.time(), director_decision=director_decision, request=request,
                before=before, after={k: request[k] for k in before},
                scope="Future registration probes; frozen identities and study deadline unchanged",
            )
            path.parent.mkdir(exist_ok=True)
            put(path, record)
            return record

    def registration_deadline(self):
        return min(self.manifest["deadline"], self.manifest.get("finalization_policy", {}).get(
            "compute_deadline", self.manifest["deadline"]
        ))

    def registration_status(self):
        return dict(
            policy=self.registration_policy(), limits=self.registration_limits(),
            recent_attempts=sorted(self._all("registration_attempts"),
                                   key=lambda r: r["created_at"], reverse=True)[:5],
        )

    def probe_registration(self, transport, *, name, machine, validate=None):
        from .execution_pool import WorkerUnavailable
        from .research_service import put

        limits = self.registration_limits()
        for attempt in range(1, limits["max_attempts"] + 1):
            remaining = self.registration_deadline() - time.time()
            if remaining <= 0:
                raise ValueError(
                    "Registration compute budget exhausted; protected report reserve retained"
                )
            timeout = min(limits["timeout_seconds"], remaining)
            started, elapsed = time.time(), time.monotonic()
            record = dict(
                id="probe_" + uuid.uuid4().hex, created_at=started,
                machine=machine, capability=name, attempt=attempt,
                max_attempts=limits["max_attempts"], timeout_seconds=timeout,
            )
            error = None
            try:
                probe = transport.call("probe", timeout=timeout)
                if validate is not None:
                    probe = validate(probe)
                record["status"] = "verified" if validate is not None else "responded"
            except (WorkerUnavailable, ValueError) as failure:
                error = failure
                record.update(status="unavailable" if isinstance(failure, WorkerUnavailable)
                              else "rejected", error=str(failure)[:1000],
                              error_type=type(failure).__name__)
            record.update(finished_at=time.time(), elapsed_seconds=time.monotonic() - elapsed)
            with self.lock():
                path = self.root / "registration_attempts" / (record["id"] + ".json")
                path.parent.mkdir(exist_ok=True)
                put(path, record)
            if error is None:
                return probe
            if not isinstance(error, WorkerUnavailable):
                raise error
            if attempt == limits["max_attempts"]:
                raise WorkerUnavailable(
                    f"Registration probe for {name} on {machine} "
                    f"failed after {attempt} attempt(s): "
                    f"{error}. See registration.recent_attempts in lab.status(); ask the director "
                    "to tune registration limits if delegated. Frozen identities remain required."
                ) from error
            time.sleep(min(5.0, attempt, max(0, self.registration_deadline() - time.time())))
