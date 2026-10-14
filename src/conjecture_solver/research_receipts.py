"""Compact receipt projections with immutable, selectively readable inventories."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field


class InventorySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    names: list[str] = Field(default_factory=list, max_length=16)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=16, ge=1, le=32)


def inventory_reference(service, record):
    entries = record.get("remote_artifacts")
    if not entries:
        return None
    payload = json.dumps(
        dict(experiment=record["id"], entries=entries), sort_keys=True, allow_nan=False
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    directory = service.root / "artifact_indexes"
    if directory.is_symlink() or not directory.resolve().is_relative_to(service.root):
        raise ValueError("Artifact index must remain inside the study")
    directory.mkdir(exist_ok=True)
    path = directory / (digest + ".json")
    if path.exists():
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Recorded artifact index changed; retrieval rejected")
    else:
        # Concurrent writers produce identical content at this content-addressed path.
        import os
        import uuid

        temporary = path.with_name("." + digest + "." + uuid.uuid4().hex)
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    return dict(path=str(path.relative_to(service.root)), sha256=digest, count=len(entries))


def receipt_projection(service, record):
    """Keep execution/source/failure identity; move only bulky transport metadata."""
    from .research_service import sha

    projected = {k: v for k, v in record.items() if k != "remote_artifacts"}
    reference = inventory_reference(service, record)
    if reference:
        projected["remote_artifact_index"] = reference
    path = service.root / "experiments" / (record["id"] + ".json")
    projected["receipt_reference"] = dict(
        path=str(path.relative_to(service.root)), sha256=sha(path)
    )
    execution = dict(projected.get("execution") or {})
    for key in ("stdout", "stderr"):
        text = execution.get(key)
        if isinstance(text, str) and len(text.encode()) > 16384:
            execution[key] = text[-8000:]
            execution[key + "_excerpt"] = True
            execution[key + "_original_bytes"] = len(text.encode())
    if execution:
        projected["execution"] = execution
    return projected


def selected_inventory(service, record, selection):
    selection = InventorySelection.model_validate(selection)
    reference = inventory_reference(service, record)
    if not reference or reference["sha256"] != selection.sha256:
        raise ValueError("Artifact inventory identity differs from the reviewed index")
    path = service.root / reference["path"]
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != selection.sha256:
        raise ValueError("Recorded artifact index changed; retrieval rejected")
    document = json.loads(payload)
    entries = document["entries"]
    names = (
        selection.names or sorted(entries)[selection.offset : selection.offset + selection.limit]
    )
    if any(name not in entries for name in names):
        raise ValueError("Requested file is absent from the recorded inventory")
    result = dict(index=reference, entries={name: entries[name] for name in names})
    if not selection.names:
        result["next_offset"] = (
            selection.offset + len(names) if selection.offset + len(names) < len(entries) else None
        )
    if len(json.dumps(result).encode()) > 12000:
        raise ValueError("Inventory excerpt exceeds 12 KiB; select fewer file entries")
    return result
