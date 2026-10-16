---
name: cst-geometry
description: Inspect, create, or modify CST Studio Suite geometry through an existing Windows installation, including imported solids, rounded tips, and complete cross-section comparisons. Use for CST CAD edits and geometry feasibility checks; the simjecture cst CLI separately supports existing-parameter magnetostatic jobs.
---

# CST geometry editing

Inspect the actual model, make the requested change on a separate project, and
verify the entire resulting shape. A successful macro, positive volume, or saved
project establishes less than a correctly shaped, smooth, simulation-ready model.

This skill documents trusted operator-side automation. It does not add a geometry
command to `simjecture cst`, install CST, or grant access outside an agent's host
permissions. Use the existing installation's Python/VBA help for exact APIs.
The workflow has been exercised with CST 2024.4; recheck other releases.

For a scripted edit through the shared runner, inspect
`simjecture windows describe --application cst` and use its `project.python`
operation. The supplied `project` is an owned working copy; record construction
with its native API and write inspection results to the script's `result`
dictionary. This is a general native-script operation, not a dedicated geometry
command or a claim that the edited assembly has been validated.

## Inspect before choosing an edit

- Establish the geometry units, project type, solver, shape names, materials,
  instances, parameters, and relevant history. A name can identify many bodies.
  Read saved metadata and query CST rather than inferring shape from a screenshot.
- Distinguish a parameterized native feature from an imported CAD solid. Imported
  ACIS/BRep geometry can support local CST edits without the original SolidWorks
  file. Original CAD helps when the requested redesign depends on its constraints,
  feature tree, or mating geometry; ask for it when that information matters.
- Locate the requested face geometrically. Do not assume a face with a similar
  radius is the intended tip. Identify which body or instances the user means.
  If unspecified, a clearly labeled single-body trial can demonstrate feasibility;
  applying it across an assembly requires the intended scope.
- Trace booleans and neighboring volumes. A ring may have been used to cut a
  dielectric or surrounding domain. Replacing the conductor alone can leave old
  cavities, overlaps, or invalid face/port references.

Read [Windows automation](references/windows-automation.md) when connecting to
CST, handling non-ASCII shape names, or recording an edit in history.

## Work on a reproducible copy

Keep the original `.cst` and its companion `Model` inputs unchanged unless the
user explicitly requests an in-place edit. Use a new output directory, record the
source inventory/hashes, and check that the source and copied inputs remained
consistent during copying. Record external dependencies rather than assuming a
copied `.cst` is self-contained. Keep unsaved source edits out of the comparison.

Use an independently owned frontend and bounded process lifetime for unattended
automation. Leave the user's existing CST session alone. Close the projects and
processes you created; do not terminate CST processes by executable name.

Prefer an existing feature parameter when it expresses the intended change.
For an imported solid, try a local face operation on an isolated copy before
changing the assembly. For a major profile redesign, rebuilding an explicit
sketch/revolved solid or reimporting revised CAD may express the design better.

Record geometry construction, imports, and modifications with
`model3d.add_to_history`. Use control macros for inspection and exports. An import
executed only as a control macro can leave a visible solid whose later edit fails
when the project history is rebuilt.

## Check the whole result

Read [Geometry and profile verification](references/geometry-verification.md)
before accepting an edit or creating before/after figures. In particular:

- Rebuild the recorded history, save to a new filename, reopen, and inspect or
  export the reopened geometry. Preserve required CAD inputs with the deliverable.
- Compare the complete section profile and neighboring faces, with equal axis
  scales and consistent coordinates. Include separate originals, an overlay, and
  enlarged views of changed joins when useful. A tip-only arc plot can conceal
  important changes.
- Check new edges and tangent continuity. Offsetting a rounded face can enlarge
  its radius while inserting short cylindrical/conical connectors with sharp
  corners. `SetConsiderBlends True` is not a guarantee of a smooth transition.
- Check materials, body count, volume, assembly interfaces, and affected references
  in proportion to the edit. Geometry checks and electromagnetic validation are
  separate outcomes. Do not start a solver merely to inspect or edit geometry.

## Leave a clear handoff

Report the exact input and new output paths; which body/instances changed; the
before/after dimensions and units; the complete profile comparison; checks that
actually passed; and any remaining geometry or assembly issues. State whether the
output contains an isolated part or the full assembly, and whether a solver ran.

Keep a short machine-readable record alongside substantial edits: source hashes,
CST version, units, target names, operation/history, output paths, observations,
and limitations. Keep private project files and proprietary manuals outside the
public repository. Skill instructions and a local demonstration do not establish
that a feature is implemented in the CLI or included in a published PR.
