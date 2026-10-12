"""Recorded storage allowances, with explicitly delegated director authority."""

from __future__ import annotations

import sys
import time

from pydantic import BaseModel, ConfigDict, Field


class StorageIncrease(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_experiment_bytes: int = Field(strict=True, gt=0, le=sys.maxsize)
    max_total_bytes: int = Field(strict=True, gt=0, le=sys.maxsize)
    reason: str = Field(min_length=16, max_length=2400)


class ResourceService:
    def resource_policy(self):
        import json

        path = self.root / "resource_policy.json"
        return json.loads(path.read_text()) if path.exists() else {"director_can_increase": False}

    def configure_resource_policy(self, *, director_can_increase, reason):
        """Operator-only delegation; deliberately absent from the worker RPC API."""
        from .research_service import put

        if (
            type(director_can_increase) is not bool
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("Require explicit director authority and an operator reason")
        with self.lock():
            path = self.root / "resource_policy.json"
            if path.exists():
                if self.resource_policy()["director_can_increase"] != director_can_increase:
                    raise ValueError("Resource delegation is immutable within a study")
                return self.resource_policy()
            policy = dict(
                director_can_increase=director_can_increase,
                reason=reason,
                created_at=time.time(),
            )
            put(path, policy)
            return policy

    def storage_limits(self):
        limits = {k: self.manifest[k] for k in ("max_experiment_bytes", "max_total_bytes")}
        for index, record in enumerate(
            sorted(self._all("storage_adjustments"), key=lambda r: r["sequence"]), start=1
        ):
            if record["sequence"] != index or record["before"] != limits:
                raise ValueError("Storage allowance history is inconsistent")
            limits = record["after"]
        return dict(limits)

    def increase_storage(self, request, *, director_decision):
        """Host action following a director decision, never a worker self-grant."""
        from .research_service import fingerprint, put

        request = StorageIncrease.model_validate(request).model_dump()
        if not isinstance(director_decision, str) or not director_decision.startswith("director_"):
            raise ValueError("Require the originating director decision")
        with self.lock():
            if not self.resource_policy()["director_can_increase"]:
                raise ValueError("Operator has not delegated storage increases to the director")
            directory = self.root / "storage_adjustments"
            identifier = "storage_" + fingerprint(director_decision)[:24]
            path = directory / (identifier + ".json")
            if path.exists():
                record = self._read("storage_adjustments", identifier)
                if record["request"] != request:
                    raise ValueError("Director decision already granted a different allowance")
                return record
            if time.time() >= self.manifest["deadline"]:
                raise ValueError("Study deadline exhausted")
            before = self.storage_limits()
            after = {k: request[k] for k in before}
            if (
                any(after[k] < before[k] for k in before)
                or after["max_total_bytes"] < after["max_experiment_bytes"]
            ):
                raise ValueError(
                    "Storage increases cannot reduce allowances; total must cover an experiment"
                )
            record = dict(
                id=identifier,
                sequence=len(self._all("storage_adjustments")) + 1,
                created_at=time.time(),
                director_decision=director_decision,
                request=request,
                before=before,
                after=after,
                scope="Future experiment admissions; existing reservations and deadline unchanged",
            )
            directory.mkdir(exist_ok=True)
            put(path, record)
            return record
