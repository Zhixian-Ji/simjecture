---
name: cst
description: Inspect saved CST 0D/1D results or commission existing parameterized magnetostatic CST projects through an operator-configured Windows installation from WSL. Use for CST Studio Suite; this bridge requires host tools and does not expose an evidence-stage lab.run capability.
---

# CST Studio Suite

Use the operator's existing Windows installation and compatible Windows Python.
Simjecture remains on WSL. The `simjecture cst` CLI records exploratory host jobs;
the Windows runner operates with its Windows account's access. Linux Bubblewrap
does not isolate these processes. Use the bridge only through authorized host
tools, never by bypassing a study's numerical sandbox or permission policy.

Read the installed CST Python and VBA documentation for its exact release.
`cst check` loads the APIs without starting a solver; a pass establishes interface
availability only. The built-in Python is used when present; `--python` selects
another Windows interpreter compatible with that CST release.

The existing CST commands use the common Windows application runner. Discover
operations with `simjecture windows describe --application cst`. Its
`project.python` operation stages a trusted Python script, opens a working project
and supplies `project`, `environment`, `work_directory`, `parameters` and `result`.
Use that operation for native modeling scripts beyond the structured magnetostatic
recipe. It saves/closes the owned copy, records artifacts and does not implicitly
run a solver. Leave JSON-serializable observations in `result`; preserve model
history and verify geometry separately.

For creating or editing solids, imported CAD, rounded tips, or complete profile
comparisons, read the separate `cst-geometry` skill (`read_skill(name="cst-geometry")`
for API agents). It documents authorized native automation; geometry editing is
not a command implemented by this bridge.

## Existing projects

Ask for a saved, self-contained project and the required observables. Inspect it
with `simjecture cst inspect --project PATH`; repeated `--result TREE_PATH` reads
the existing run IDs, parameter combinations, axis labels and numeric data.
`--allow-open` permits reading an open, saved project. Unsaved changes and active
calculations are not supported by the saved-results reader.
Run ID 0 may repeat a numbered run's current result; it is not another replicate.

`simjecture cst submit` currently runs **LF MStatic** only. It copies the project
and its `Model` directory into a fresh job on a Windows drive. The original
project and result folders are preserved. External dependencies must already be
available to the Windows account; their automatic staging is not implemented.

- Declare finite numeric parameter values with repeated `--parameter NAME=NUMBER`.
  Model expressions and arbitrary VBA snippets are not accepted by this interface.
- Declare the exact 0D/1D tree paths to extract using repeated `--result` options.
  Derive these from project inspection, rather than inventing names or units.
- Supply a Windows-drive `--job-root` visible from WSL and a bounded `--timeout`.
  Jobs serialize per installation. The deadline includes queue time.
- Follow the returned directory using `cst status`, `cst wait` or `cst cancel`.
  A cancellation request is not a confirmed stop until the supervisor reports it.

The runner creates a new CST instance, deletes results **in its working copy**,
updates parameters, rebuilds and solves. Retained sample outputs and parameter
sweep caches must never be accepted as a fresh observation.

## Interpret results

Receipts have `stage="exploration"` and `evidence_eligible=false`. They do not
grant production methods approval or scientific claim support. CST is not yet a
registered Linux/SSH execution-pool capability. A native agent can use this CLI
for authorized instrument preparation; API agents restricted to sandboxed
execution must report that boundary instead of attempting a host escape.

For magnetostatic inductances, inspect the declared winding currents, turns,
geometry units, permeability model, boundaries and mesh settings. Check matrix
reciprocity, positive magnetic energy, flux linkage and energy consistency. A
relative energy change used for mesh stopping is not an error bound on every
matrix entry. Qualify boundary placement and mesh refinement for the intended use.

No CST code, binary, project, license file or proprietary documentation is shipped
with this integration. Runtime and project files remain with the operator.
