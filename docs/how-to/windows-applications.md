# Control Windows applications through a common job interface

`simjecture windows` provides an experimental operator-side interface for installed
Windows engineering applications. Simjecture runs in WSL; a shared Windows runner
manages jobs and dispatches application-specific work to an adapter.

```text
Simjecture CLI / Python client
             |
             v
Common request, snapshot, supervisor and artifact protocol
             |
        application adapter
          /           \
   CST Python/VBA   SOLIDWORKS COM
```

The shared protocol handles discovery, complete input snapshots, a frozen runner
bundle, asynchronous execution, deadlines, cancellation, application process
ownership and output hashes. Adapters supply model staging, operation validation
and native API calls. Product-specific geometry selections, parameter meanings,
units and solver settings remain explicit.

## Discover and submit operations

Discovery works without Windows Python or a vendor installation:

```bash
simjecture windows describe
simjecture windows describe --application cst
simjecture windows describe --application solidworks
```

Each adapter lists its operations, argument meanings, project types, runtime
requirements and qualification limits. `check` loads an installed interface; it
does not start a model or qualify a numerical result:

```bash
simjecture windows check --application cst \
  --installation '/mnt/c/Program Files (x86)/CST Studio Suite 2024' \
  --python '/mnt/c/Program Files (x86)/CST Studio Suite 2024/Opera/code/bin/python.exe'
```

Native agents can read the `windows-applications` skill. API agents can discover
and read it with `read_skill(name="windows-applications")`; this supplies interface
guidance and does not grant Windows host execution permissions.

The common submission form selects an application and an operation. Arguments
are a JSON object validated by that adapter before a job is allocated:

```bash
simjecture windows submit --application cst \
  --installation '/mnt/c/Program Files (x86)/CST Studio Suite 2024' \
  --python '/mnt/c/Program Files (x86)/CST Studio Suite 2024/Opera/code/bin/python.exe' \
  --project '/mnt/c/work/Two Coils.cst' \
  --job-root '/mnt/c/work/application-jobs' \
  --operation magnetostatic.solve \
  --arguments '{"parameters":{"r":2},"results":["1D Results\\Ms Solver\\Inductance Matrix\\L coil1, coil1"]}' \
  --timeout 600
```

The existing [`simjecture cst` commands](cst-windows.md) remain available and submit
through the same shared runner. CST numeric parameter values use the model's
native units.

Use the returned job directory with any of the common lifecycle commands:

```bash
simjecture windows status '/mnt/c/work/application-jobs/cst_JOB_ID'
simjecture windows wait '/mnt/c/work/application-jobs/cst_JOB_ID'
simjecture windows cancel '/mnt/c/work/application-jobs/cst_JOB_ID'
simjecture windows artifacts '/mnt/c/work/application-jobs/cst_JOB_ID'
```

`cancel` requests a stop and returns successfully when the request is recorded.
`wait` returns exit code 130 for a confirmed cancellation and a nonzero exit code
for failed, timed-out or interrupted jobs. A lost supervisor is reported as an
unconfirmed stop. `artifacts` verifies the saved result manifest and every returned
file hash before returning the list.

## Native CST scripts

The `project.python` operation opens a copied CST project and executes an explicitly
supplied trusted Python script using the installed CST APIs. This reaches modeling
features beyond the structured magnetostatic recipe. It does not automatically
launch a solver.

The script receives `project`, `environment`, `work_directory`, `parameters`, and
an initially empty `result` dictionary. Leave JSON-serializable observations in
`result`; generated files under `work_directory` become artifacts. The adapter
saves and closes its working project and application after the script.

```python
# automation.py: use the supplied, owned project for native CST API operations.
result = {"parameters_received": parameters}
```

```bash
simjecture windows submit --application cst \
  --installation '/mnt/c/Program Files (x86)/CST Studio Suite 2024' \
  --python '/mnt/c/Program Files (x86)/CST Studio Suite 2024/Opera/code/bin/python.exe' \
  --project '/mnt/c/work/model.cst' --job-root '/mnt/c/work/application-jobs' \
  --operation project.python --script ./automation.py \
  --arguments '{"parameters":{},"results":[],"script":"inputs/automation.py"}'
```

The script is included in the input snapshot. This is trusted Windows host code,
with the account's access. Read the installed CST API documentation and the
`cst-geometry` skill for modeling history, geometry and assembly verification.

## Experimental SOLIDWORKS part operations

The SOLIDWORKS adapter requires a registered desktop SOLIDWORKS COM server and
`pywin32` in the selected Windows Python interpreter. Its initial operation supports
self-contained `.sldprt` parts, dimension changes, rebuilding and STEP/IGES/STL
exports. It does not stage assembly references or run SOLIDWORKS Simulation.

```bash
simjecture windows submit --application solidworks \
  --installation '/mnt/c/Program Files/SOLIDWORKS Corp/SOLIDWORKS' \
  --python '/mnt/c/Python312/python.exe' \
  --project '/mnt/c/work/part.sldprt' --job-root '/mnt/c/work/application-jobs' \
  --operation part.rebuild_export \
  --arguments '{"dimensions_si":{"D1@Sketch1":0.02},"exports":["step"]}'
```

Dimension names must come from the actual part. Values are native API SI values:
metres for lengths and radians for angles, irrespective of the model display
units. The adapter checks the rebuilt values and clears selections before export
to avoid exporting only selected faces or bodies.

COM activation can create a process outside the worker's descendant tree. The
adapter checks the returned PID's creation time, assigns the new process to the
supervisor's named Windows Job Object and checks its executable against the chosen
installation before opening a model. An existing operator session is refused and
left open. The adapter's COM objects are released on the same initialized thread.
If COM activation fails or hangs before a process can be identified and adopted,
the receipt is `interrupted`, with the stop explicitly unconfirmed and activation
details retained in `external-activation.json`. Inspect and reconcile that
activation before starting another job on the installation.

This adapter is experimental and has no claimed native SOLIDWORKS model
commissioning. Contract tests use mocked COM objects; native Windows tests cover
the real process-adoption mechanism without SOLIDWORKS. Test an actual part and
its exported geometry before depending on the adapter for a design workflow.

## Adapter and protocol contract

Each built-in adapter implements:

- `describe()`: supported operations, units, project types and requirements;
- `check(installation)`: load the native interface without starting a model;
- `input_sources(project, script)`: map source files/directories to snapshot paths;
- `validate(operation, arguments, inputs)`: reject unsupported or ambiguous tasks;
- `execute(job, request, context)`: operate on `job/work`, close the owned
  application and return a JSON object. Use `context["begin_external_activation"]`
  before activating an external COM server and `context["claim_process"]` to adopt
  its new PID before model access.

Keep discovery, staging and validation importable on Linux without vendor packages.
Native modules use Python 3.9-compatible stdlib code and import optional vendor/COM
libraries only when needed. Register a new adapter in `windows_runtime.ADAPTERS`;
its source then joins the complete per-job runtime bundle. A future COMSOL adapter
can use its Java API or batch interface behind this contract. COMSOL execution is
not implemented or advertised by the current registry.

Requests use protocol `simjecture-windows/1` and bind the application, operation,
arguments, input file inventory, complete native source bundle, selected Windows
Python executable hash, creation time and timeout. The worker rejects added,
removed or changed input/runtime files and verifies its writable copy before any
application call. Terminal receipts bind `results.json`; that file contains the
working output inventory and hashes. Historical CST terminal records remain
readable through the lifecycle commands.

The native runner can be invoked directly by Windows Python; it does not import
the Linux Simjecture package. The current client uses local WSL interoperability
and Windows-drive job directories. Remote Windows transport, a governed
`lab.run` execution pool, Windows CPU/RAM quotas and a persistent session API are
outside this iteration. All jobs remain exploratory with `evidence_eligible=false`.

## Validation

`pytest tests/test_windows_applications.py tests/test_cst.py` covers the common
protocol, complete input inventories, working-copy integrity, native-script
dispatch, cancellation exit codes and mocked SOLIDWORKS operations.

`python scripts/check_cst_windows.py` runs the shared native Windows process tests
without vendor applications or additional packages. It includes a frozen-bundle
fixture adapter executed through the real detached supervisor and worker. The
fixture establishes orchestration behavior, not vendor API or solver accuracy.
`--runner PATH` can select a packaged runner; the original `cst_windows.py`
entrypoint remains covered separately for compatibility.
