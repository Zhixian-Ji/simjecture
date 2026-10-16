# Windows automation for geometry work

## Find the installed interfaces

Use the operator's installed CST Python libraries and a compatible Windows Python.
Simjecture can remain in WSL. In a standard CST 2024 installation, useful locations
relative to the installation directory are:

| Resource | Relative path |
| --- | --- |
| Python libraries | `AMD64/python_cst_libraries` |
| Bundled Python, if present | `Opera/code/bin/python.exe` |
| Python API help | `Online Help/Python/source/cst.interface.html` |
| VBA help root | `Online Help/mergedProjects/VBA_3D` |

Search the VBA help for `Solid`, `Pick`, `LocalModification`, `SAT`, `Plot`, and
the native modeling object relevant to the requested operation. Consult the local
manual; do not ship it with the skill. A successful API import does not prove that
the frontend, model, or solver can run.

Convert every WSL path used by native Python or VBA to a Windows path, including
the project, output, script, and import paths. `wslpath -w` or Simjecture's
`conjecture_solver.cst.windows_path` handles mounted drives and WSL UNC paths.
An input manifest can contain Linux paths; converting the manifest filename does
not convert the paths inside it. Pass subprocess arguments as an argument list.

Simjecture's `cst_windows.py` contains internal, release-specific helpers for
loading CST, launching a separate frontend and owning a Windows Job Object. Check
their current implementation before reusing them. For unattended work, assign a
gated worker to the Windows Job Object **before** it launches CST, and enforce a
deadline in a native Windows supervisor. A timeout on the WSL caller alone does
not stop its Windows descendants. No process may be left owning the project after
the supervisor reports cleanup complete.

Never use the LF MStatic submission command to inspect or run a project with a
different solver. Geometry-only work does not require starting a solver.

## Inventory shapes and identify faces

Useful CST 2024 methods include:

- `project.project_type()`, `model3d.get_active_solver_name()`, and
  `model3d.is_solver_running()`.
- `Solid.GetNumberOfShapes`, `Solid.GetNameOfShapeFromIndex(index)`,
  `Solid.GetMaterialNameForShape(name)`, and `Solid.GetVolume(name)`.
- `Solid.GetLooseBoundingBoxOfShape(name, xmin, xmax, ymin, ymax, zmin, zmax)`:
  inspect in a control macro; these are loose global bounds, not exact dimensions.
- `Pick.GetFaceIdFromPoint(name, x, y, z)` and `Pick.PickFaceFromId(name, id)`:
  query a point on the actual trimmed face and confirm the selection. Face IDs
  are specific to the model state and may change after topology changes.

Select by verified full body path or a predicate that resolves uniquely. Do not
copy a face ID or select the first shape from another example. Iterate valid
shape indices according to the installed API, skipping empty names.

Some CST 2024.4 Python tree calls failed with `UnicodeDecodeError` on Chinese
names in a Windows code-page environment. If encountered, query the names inside
VBA instead of renaming user objects or decoding corrupted Python results:

1. Enumerate native names with `Solid.GetNameOfShapeFromIndex`.
2. Construct non-ASCII search strings using `ChrW` when the Python-to-VBA string
   boundary is unreliable; keep the transmitted macro itself ASCII.
3. Keep the returned native name in a VBA variable for `SAT.Write`, `Pick`, and
   local modifications.
4. If using `Open ... For Output` to write an inventory, record the Windows ANSI
   code page and decode accordingly. For example, `gb18030` can read code-page
   936 output. Do not silently discard undecodable characters. A UTF-8 writer
   verified on that machine is another option.

In Python, the Windows ANSI code page can be queried using
`ctypes.windll.kernel32.GetACP()`. This workaround is conditional on the actual
encoding failure; Unicode behavior varies by environment and release.

## Persist imports and edits

For a new project, choose `DesignEnvironment.new_mws()` or `new_ems()` according
to the intended physics, then explicitly define units, materials, components and
native features in modeling history. Choose construction objects from the
installed VBA help. A saved geometry project alone does not provide the ports,
boundaries, mesh or solver setup of a complete simulation.

`project.schematic.execute_vba_code("Sub Main()\n...\nEnd Sub")` executes a control
macro. Use it for inventories, selection checks, exports, and screenshots.

`project.model3d.add_to_history(header, body)` executes and records a modeling
block; `body` omits the surrounding `Sub Main` / `End Sub`. Include construction
and imports before modifications. For example, an isolated SAT import uses a
history block with the following body, after setting the new project's intended
units and substituting a Windows path:

```vb
With SAT
    .Reset
    .FileName "C:\work\geometry-review\original-part.sat"
    .Id "1"
    .ScaleToUnit True
    .Read
End With
```

Do not change an existing project's unit system merely to fit an example. Keep
import dependencies alongside the output, or verify CST has retained everything
needed before calling the project self-contained.

An illustrative local-offset history body follows. Replace `target_name`,
`validated_face_id`, and `offset_expression` with values derived from inspection;
they are explanatory symbols, not CST globals:

```vb
Pick.ClearAllPicks
Pick.PickFaceFromId target_name, validated_face_id
With LocalModification
    .Reset
    .Name target_name
    .SetConsiderBlends True
    .EnableInvertedFaceRemoval False
    .OffsetSelectedFaces offset_expression
End With
Pick.ClearAllPicks
```

The offset is along the face normal in project geometry units. Export and measure
the result; sign and topology determine what actually changed. If exposing a
parameter, store it deliberately and check its units and rebuilt value.

`LocalModification.DefineConstraintSelectedFaceRadius` is documented for
**cylindrical** faces; it is not a general torus-minor-radius setter. Offsetting
a toroidal face can change its minor radius while adding connecting faces.

## Rebuild, save, reopen, and export

CST 2024 exposes `model3d.full_history_rebuild(timeout=...)`. Check the method's
behavior for the installed release, allow a bounded operation, and inspect the
result and errors. A control macro using `Rebuild()` can also check a rebuild;
raise or record a failure if it returns false. Never accept a merely visible
cached solid as proof that its complete construction history works.

Save explicitly with
`project.save(new_windows_path, include_results=False, allow_overwrite=False)`
when delivering a geometry-only project to a new filename. Close the working
project and reopen that saved path in the owned frontend, then remeasure/export
the reopened geometry. If claiming portability, verify the project with its
declared dependencies rather than relying on an undeclared external import.

Useful native exports in a control macro:

```vb
With SAT
    .Reset
    .FileName "C:\work\geometry-review\modified-part.sat"
    .ExportFromActiveCoordinateSystem False
    .Write target_name
End With
```

For a model-view image, select the model tree, choose a view with
`Plot.RestoreView`, call `Plot.ZoomToStructure`, and use
`Plot.ExportImage(filename, width, height)`. A whole-ring perspective can hide a
small profile defect; it complements the full section rather than replacing it.

Finally compare the source inventory/hashes again, close the created projects and
frontend, and confirm the owned processes exited while pre-existing sessions
remain present. Keep work-copy logs and a concise handoff with the new project.
