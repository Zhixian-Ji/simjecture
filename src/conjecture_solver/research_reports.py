"""Independent report requests, separate from scientific claim adjudication."""

from __future__ import annotations

import hashlib
import json
import time


class ReportService:
    def reconcile_report_assessments(self):
        """Recover a queued request already fulfilled by the bound final assessor."""
        from .research_finalization import ReportAssessment
        from .research_service import put, sha

        path = self.root / "finalization.json"
        if not path.exists():
            return
        try:
            final = json.loads(path.read_text())
            if final.get("report_status") not in {"reviewed", "needs_revision"}:
                return
            verdict = ReportAssessment.model_validate(final["assessment"]).model_dump(mode="json")
            for key in ("report", "packet"):
                if not isinstance(final.get(key), str):
                    return
                target = (self.root / final[key]).resolve()
                if not target.is_relative_to(self.root) or sha(target) != final[key + "_sha256"]:
                    return  # A changed snapshot cannot fulfil any request.
        except (OSError, KeyError, ValueError):
            return  # Incomplete historical metadata is not an accepted assessment.
        for record in self._all("report_requests"):
            if (
                record["status"] in {"queued", "running", "failed", "deferred"}
                and record["report_sha256"] == final["report_sha256"]
                and record["packet_sha256"] == final["packet_sha256"]
                and record["report"] == final["report"]
                and record["packet"] == final["packet"]
            ):
                if record.get("error"):
                    record.setdefault("previous_errors", []).append(record.pop("error"))
                record.update(
                    status="resolved",
                    assessment=verdict,
                    resolved_at=final.get("finished_at") or final.get("updated_at"),
                    reviewed_during_finalization=True,
                )
                put(self.root / "report_requests" / (record["id"] + ".json"), record)

    def freeze_report(self, report="RESULTS.md"):
        from .research_service import fingerprint, put, sha

        path = self._source(report)
        if path.stat().st_size > 512 * 1024 or not path.read_text().strip():
            raise ValueError("Supply a nonempty UTF-8 report of at most 512 KiB")
        body = path.read_bytes()
        snapshot = self.status(compact=True)
        # Time and previous report requests cannot create duplicate requests for
        # the same report and scientific record.
        identity = dict(
            report_sha256=hashlib.sha256(body).hexdigest(),
            protocol_sha256=self.manifest.get("protocol_sha256"),
            records={
                kind: [[p.stem, sha(p)] for p in sorted((self.root / kind).glob("*.json"))]
                for kind in ("experiments", "methods", "reviews", "commitments")
            },
        )
        identifier = fingerprint(identity)
        directory = self.root / "report_reviews" / identifier
        packet = dict(
            original_hypothesis=self.manifest["hypothesis"],
            operator_protocol=self.manifest.get("operator_protocol"),
            study_root=str(self.root),
            scientific_snapshot=snapshot,
            continuation=self.manifest.get("continuation", {}),
            report=body.decode(),
            report_sha256=identity["report_sha256"],
            source_report=str(path.relative_to(self.root)),
            record_identity=identity,
            scope="Report assessment only; does not approve claims or change evidence stages.",
        )
        with self.lock():
            directory.mkdir(parents=True, exist_ok=True)
            if (directory / "packet.json").exists():
                saved = json.loads((directory / "packet.json").read_text())
                if (
                    saved["record_identity"] != identity
                    or sha(directory / "RESULTS.md") != identity["report_sha256"]
                ):
                    raise ValueError("Frozen report changed; assessment rejected")
                packet = saved
            else:
                (directory / "RESULTS.md").write_bytes(body)
                put(directory / "packet.json", packet)
        return directory, packet

    def assess_report(
        self, report="RESULTS.md", *, reason="Assess the retained scientific findings and limits"
    ):
        from .research_service import put, sha

        if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 2400:
            raise ValueError("Report assessment requires a concrete reason")
        if time.time() >= self.manifest["deadline"]:
            raise ValueError("Campaign deadline reached")
        frozen, packet = self.freeze_report(report)
        identifier = "report_" + frozen.name[:24]
        with self.lock():
            directory = self.root / "report_requests"
            directory.mkdir(exist_ok=True)
            path = directory / (identifier + ".json")
            if not path.exists():
                put(
                    path,
                    dict(
                        id=identifier,
                        status="queued",
                        created_at=time.time(),
                        reason=reason.strip(),
                        report=str((frozen / "RESULTS.md").relative_to(self.root)),
                        source_report=packet["source_report"],
                        packet=str((frozen / "packet.json").relative_to(self.root)),
                        report_sha256=packet["report_sha256"],
                        packet_sha256=sha(frozen / "packet.json"),
                        authority="Report review only; never scientific claim acceptance",
                    ),
                )
        return self.report_assessment_status(identifier)

    def report_assessment_status(self, identifier):
        from .research_service import sha

        record = self._read("report_requests", identifier)
        for key in ("report", "packet"):
            path = (self.root / record[key]).resolve()
            if not path.is_relative_to(self.root) or sha(path) != record[key + "_sha256"]:
                raise ValueError("Frozen report or packet changed; assessment rejected")
        source = self.root / record.get("source_report", "research/RESULTS.md")
        record["current_report_unchanged"] = (
            source.is_file()
            and not source.is_symlink()
            and source.resolve().is_relative_to(self.work)
            and sha(source) == record["report_sha256"]
        )
        return record
