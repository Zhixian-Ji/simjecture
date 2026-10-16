# Use an installed Windows CST from WSL

The experimental `simjecture cst` bridge keeps Simjecture on WSL and uses native
Windows Python to operate an existing CST Studio Suite installation. It can
inspect saved 0D/1D results and submit self-contained **LF MStatic** projects with
numeric parameter changes. Other solvers, remote Windows hosts, external input
staging and direct evidence-stage `lab.run` integration are outside this version.

The operator supplies CST, a compatible Windows Python and the necessary solver
authorization. Nothing downloads or redistributes CST. CST 2024 includes compatible
Python libraries and may include an interpreter at `Opera/code/bin/python.exe`.
Use `--python` if your installation needs a different compatible interpreter.

## Check the interface and inspect an existing result

Run these commands from WSL. A single installation in the standard
`/mnt/c/Program Files (x86)` directory is detected automatically. Otherwise use
`--installation` or set `SIMJECTURE_CST_INSTALLATION` to the installed directory.
Windows absolute paths are also accepted; quote spaces and backslashes.

```bash
simjecture cst check --installation '/mnt/c/Program Files (x86)/CST Studio Suite 2024'

simjecture cst inspect \
  --project '/mnt/c/work/examples/Two Coils.cst' \
  --result '1D Results\Ms Solver\Inductance Matrix\L coil1, coil1'
```

`check` loads the Windows APIs without launching a frontend or solver. It reports
`solver_tested=false`; this is not numerical readiness. `inspect` lists available
0D/1D tree paths and, for requested paths, returns run IDs, parameter combinations,
axis labels and values. Complex values use `{real, imag}` objects. Missing result
paths and non-finite values fail explicitly.
Run ID 0 is CST's current-result view and may duplicate a numbered run; it must
not be counted as an independent replicate.

Save and close the project before inspection. If it must remain open, use
`--allow-open` after saving and while no calculation is active. CST's reader
cannot see unsaved state. It requires unpacked, unprotected projects supported
by that installed reader version; see the local CST Python documentation.

## Submit and monitor a magnetostatic job

Save the source project. Choose a separate job root on a local Windows drive
mounted in WSL. The operator-side job directory contains the immutable input
snapshot, writable project copy, request, state, results and Windows logs.

```bash
simjecture cst submit \
  --project '/mnt/c/work/examples/Two Coils.cst' \
  --job-root '/mnt/c/work/cst-jobs' \
  --parameter r=2 \
  --result '1D Results\Ms Solver\Inductance Matrix\L coil1, coil1' \
  --result '1D Results\Ms Solver\Inductance Matrix\L coil1, coil2' \
  --result '1D Results\Ms Solver\Inductance Matrix\L coil2, coil2' \
  --timeout 600

# Use the directory returned by submit:
simjecture cst status '/mnt/c/work/cst-jobs/cst_JOB_ID'
simjecture cst wait '/mnt/c/work/cst-jobs/cst_JOB_ID'
simjecture cst cancel '/mnt/c/work/cst-jobs/cst_JOB_ID'
```

Submission is asynchronous. Jobs serialize per CST installation, including across
different job roots. Their wall deadline includes queueing. A detached Windows
supervisor owns a Windows Job Object containing the worker and its descendant
processes. Timeout, cancellation or supervisor termination closes that job and
terminates its members. The runner creates its own CST instance; it does not
connect to the operator's existing frontend or kill every CST process by name.

`cancel` records a request; inspect `status` until a terminal outcome is reported.
The supervisor runs on Windows and continues enforcing the deadline if the WSL
caller exits. Windows shutdown and loss of access to the job drive require
operator reconciliation; a stale `running` receipt must not be reported as a
confirmed successful execution or cancellation.

The source `.cst` and its companion `Model` directory are snapshotted and hashed.
Existing `Result`, `Temp`, `SP` and mesh-cache directories are not staged. The
Windows worker deletes results in its writable project copy, sets finite numeric
parameters, rebuilds and starts the magnetostatic solver. It saves the output and
extracts the declared result tree paths. The original project is preserved.
Reference files outside the project are not automatically discovered or staged;
use a self-contained starting model for this version.

Inspect `worker.log` and `supervisor.log` for failures. A successful receipt binds
the `results.json` hash; later output modification is detected by `status`.
The request also records input hashes and the runner and Python executable
identities. Retain the entire job directory for diagnosis and reproduction.

If startup fails with **WinError 740**, Windows requires elevation for that CST
executable. This can be caused by the installation's “Run as administrator”
compatibility setting. Use an operator-authorized launch context appropriate for
the installation. The bridge does not change Windows compatibility settings,
request UAC elevation or attach to an existing elevated frontend. Saved-result
inspection may still work in an ordinary WSL session when frontend startup is
blocked. Report those checks separately from a successful fresh solve.

## Commission the actual model

The official **Two Coils** example is a useful initial instrument: in CST's
Component Library, find the example and save a local copy. The documented model
uses metre geometry units, two five-turn current-driven coils and a linear
relative permeability of 1000. Its existing sweep can contain saved results.
For bridge acceptance, submit a new job for each parameter combination and
compare with a manually verified baseline.

Verify the meanings and units of the results, source current, winding orientation,
magnetic material model, boundaries and convergence controls. For linear magnetic
models, useful internal checks are matrix reciprocity, positive magnetic energy,
flux linkage and consistency of field energy with `0.5 Iᵀ L I`. These checks do
not independently prove numerical accuracy. In particular, a mesh stopping limit
on energy change does not bound the error in every self- or mutual-inductance entry.

## Execution and scientific scope

This is an **operator-side trusted Windows process** integration. The selected
Linux Bubblewrap/PRoot numerical backend neither executes nor isolates CST.
Windows account access applies; CPU/RAM reservation and monitoring from Linux
execution pools do not apply to these jobs. This initial bridge serializes jobs
and enforces a wall deadline, but does not impose Windows CPU or memory quotas.

Jobs are permanently marked `stage="exploration"` and `evidence_eligible=false`.
They are not registered `lab.run` capabilities and cannot grant evidence eligibility,
methods approval or claim support. Native agents with operator-authorized host
tools may use the bridge for preparation and diagnostic work. Sandboxed API agents
must not use it to bypass their configured execution boundary.

The `cst` skill is shipped and discoverable even without an installation.
Building a governed Windows numerical worker requires a separately reviewed
transport, resource accounting and evidence binding; it is not achieved by pointing
a Linux capability descriptor at a Windows executable.

Consult the documentation installed with your CST release, including the Python
`cst.interface` and `cst.results` modules and VBA application commands. Public
upstream references include the [CST solver overview](https://www.3ds.com/products/simulia/cst-studio-suite/electromagnetic-simulation-solvers)
and [CST Python automation overview](https://3dswym.3dexperience.3ds.com/post/simulia-community/top-10-q-a-articles-for-january-2024_SfCnArS3TgKOnGOVp4Rl8Q).
