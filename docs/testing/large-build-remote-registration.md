# Large builds, remote registration and director storage authority

An SSH research campaign exposed two independent harness defects. A CUDA solver
binary larger than 512 MiB could not link through the experiment launcher despite
a larger workspace reservation. The generated scientific client then failed on
`register_capability(name=...)`, and registration assumed a local capability directory.
Neither failure establishes anything about the scientific model.

The shared executor inherited a generic per-file resource guard. Its purpose was
to bound runaway output; the fixed value did not account for large scientific
executables. The repair makes the per-file ceiling follow the admitted workspace
allowance, retains aggregate/time/memory controls, and records the actual limits and
executor hash. Oversized input checks now happen before hashing or copying inputs.

The generated client uses `operation` for its internal dispatch argument so tool
arguments named `name` cannot collide. Registration accepts a frozen worker ID and
uses its existing readiness protocol to verify the new descriptor, original runtime
identities, worker code/configuration, capacity and clock. The append-only registration
provides an alias for execution, source inspection and source-bound methods review.
The original frozen pool and capability identities are retained.

An optional operator delegation allows the research director to increase storage
allowances with a recorded reason. It has no extra preset ceiling. Grants apply to
future admissions, survive coordinator restarts, and wake an idle worker. Prior
receipts and deadlines do not change. Shared/hosted studies do not acquire this
authority by default; the worker RPC cannot issue grants.

Regression coverage exercises a real 600 MiB sparse file through the launcher,
rejection under a smaller allowance, generated client serialization, a newly added
worker instrument executing through the frozen pool, source inspection, method binding,
immutable original identities, mismatched worker identity, director grants, idempotent
grant replay and preservation of earlier experiment reservations. These are engineering
checks, not qualification of a repaired plasma solver or scientific evidence.

Validation on 2026-10-12: 240 focused tests passed across research supervision,
director decisions, resource repairs, remote registration, experiment pools and
transport, worker polling, CPU limits, study launch, report finalization,
continuation, notebook recovery and the study interface. The browser checks cover
both desktop and mobile layouts, current allowances, director delegation and
before/after grant records. Ruff, JavaScript syntax and whitespace checks passed.

A separate readiness study on an actual SSH GPU worker registered a new descriptor
against a previously frozen pool, read its source and executed a 600 MiB sparse
output probe successfully. Its 4 GiB reservation became the measured per-file limit;
the probe removed its large temporary file. The original research campaign resumed
with the same deadline, scientific contract and original instrument hashes, and
with explicitly delegated director storage authority. No private research assets
are part of this repair or public regression suite.
