"""Campaign permission and provenance for experimental solver implementations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SolverDevelopment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_capability: str | None = None
    change_type: Literal["numerical", "physical", "diagnostic"]
    rationale: str = Field(min_length=16, max_length=2400)
    preserved_assumptions: str = Field(min_length=16, max_length=2400)
    validation_plan: str = Field(min_length=16, max_length=2400)
    build_experiments: list[str] = Field(default_factory=list, max_length=16)


class DevelopmentService:
    def solver_development_policy(self):
        """Never infer new permission for a study created before this policy existed."""
        return self.manifest.get("solver_development_policy") or dict(
            allowed=None, source="saved_operator_instructions"
        )

    def validate_development(self, development):
        from .research_methods import experiment_ids

        value = SolverDevelopment.model_validate(development).model_dump()
        parent = value["parent_capability"]
        if parent is not None and parent not in self.capability_hashes():
            raise ValueError("Choose a registered parent capability for solver development")
        value["build_experiments"] = experiment_ids(value["build_experiments"], "build_experiments")
        for identifier in value["build_experiments"]:
            record = self._read("experiments", identifier)
            if record["status"] in {"queued", "running"}:
                raise ValueError("Wait for the recorded build before registering its result")
        return value

    def solver_development_status(self, *, compact=False):
        from .research_service import fingerprint

        methods = self._all("methods")
        builds = []
        for record in self._all("capability_additions"):
            reviews = [
                {k: m.get(k) for k in ("id", "scope", "status")}
                | {
                    "decision": (m.get("verdict") or {}).get("decision"),
                    "source": m["binding"]["source"],
                }
                for m in methods
                if m["binding"]["capability"] == record["name"]
                and m["binding"]["runtime_sha256"] == record["runtime_sha256"]
            ]
            builds.append(
                dict(
                    name=record["name"],
                    runtime_sha256=record["runtime_sha256"],
                    registration_path="capability_additions/"
                    + fingerprint(record["name"])
                    + ".json",
                    created_at=record["created_at"],
                    development=record.get("development"),
                    method_reviews=reviews,
                    qualification_scope="Approval binds the reviewed method and scope",
                )
            )
        builds.sort(key=lambda r: r["created_at"], reverse=True)
        omitted = 0
        if compact:
            omitted = max(0, len(builds) - 8)
            builds = builds[:8]
            for build in builds:
                if build["development"]:
                    build["development"] = {
                        k: v[:600] if isinstance(v, str) else v
                        for k, v in build["development"].items()
                    }
        return dict(policy=self.solver_development_policy(), builds=builds, omitted_builds=omitted)


def development_guidance(policy):
    allowed = policy["allowed"]
    if allowed is True:
        permission = "Experimental solver development is ALLOWED in separate private source forks."
    elif allowed is False:
        permission = (
            "Experimental solver development is DISABLED. Inspect solver source read-only and "
            "use the supplied implementations; do not build replacement solver implementations. "
            "Experiment and analysis scripting remains available."
        )
    else:
        permission = (
            "This older study retains its saved operator instructions for solver development; "
            "no new source-editing permission is inferred."
        )
    return (
        permission
        + """
Preserve the reference installation. Native tool access is unchanged; this is a research
permission, not an operating-system sandbox setting. Explicit operator restrictions remain
binding. The worker records the defect or scientific need, assumptions to preserve and
an affordable validation plan in lab.note(kind='implementation' or 'next_test') before a build.
The director manages budget and strategy; source changes need no new human approval when
already authorized. Only the worker edits source; the director and reviewers remain read-only.
The worker saves build/source identities and uses a new capability name. Example provenance:
development={'parent_capability': 'reference-alias', 'change_type': 'numerical',
'rationale': 'Describe the defect and repair.',
'preserved_assumptions': 'Describe the retained equations and conditions.',
'validation_plan': 'Describe the required evolution and conservation tests.'}
Other change types are 'physical' and 'diagnostic'. Optional build_experiments must contain
actual completed exp_... receipt IDs. This metadata describes the change; it does not qualify it.
Numerical repairs should solve the declared equations more accurately. Changes to physical
assumptions require a declared variant and, when outside the root claim, a prospectively
committed hypothesis branch. Preserve failed controls. Independently qualify the changed
method under relevant boundaries, conservation and accuracy tests before hypothesis evidence.
Instrument-readiness approval covers only its reviewed scope, not the entire solver. A
solver implementation failure is not a physical counterexample. Publication and upstream
changes need separate authorization.
"""
    )
