# Geometry and profile verification

## Choose checks that answer the design question

A rounded-tip edit requires checking the whole profile and both joins, not only
the radius of its selected face. A valid closed body can contain an unintended
ledge or sharp corner. In an electric-field model, report such a geometry defect
before treating the trial as a finished redesign or interpreting a field maximum.

Use a small isolated trial to establish whether imported geometry is editable.
Keep that result separate from an assembly-wide edit. A single body exported
from an assembly does not contain the original ports, domains, materials setup,
or solver configuration needed to validate the assembly.

Check the following as appropriate to the operation:

| Question | Useful evidence |
| --- | --- |
| Did the intended dimension change? | Measure or export the actual resulting face and section. |
| Did construction survive? | Rebuild history, save, reopen, then query/export again. |
| Did the body remain usable? | Body type, volume, topology, and native geometry checks. |
| Did the rest of the part change? | Compare every face/profile segment, not just the tip. |
| Are the joins smooth? | Inspect new edges and tangent directions at adjacent faces. |
| Does the assembly still fit? | Check cuts, neighboring volumes, gaps, overlaps, ports and face references. |
| Has simulation behavior been checked? | Only a declared fresh solve and appropriate mesh/physics checks establish this. |

`Solid.IsSolidShape` and a positive `Solid.GetVolume` are useful observations,
not complete validity tests. The documented solid-type query can also be true
for a body that is not a pure sheet. If using it during history replay, consult
`Solid.UpdateAllBodyTypes`. CST's native solid checker offers deeper checks;
consult the installed help because some checks open interactive windows.

## Make the complete section visible

Use a native section/export operation supported by that release, or intersect an
exported CAD solid using a suitable geometry kernel. Preserve the exact imported
profile, including grooves, fillets, small faces, and any new connector faces.
Do not replace it with an idealized torus or draw only an untrimmed analytical arc.

Use the same section plane, coordinate frame, origin and units for both versions.
Set equal scale on the two axes so angles and curvature are not visually distorted.
For a rotational ring, show the entire radial profile, with its position relative
to the rotation axis labeled. A full-diameter view of two widely separated narrow
sections can be an additional locator view; it does not replace a readable part
profile.

A useful comparison includes original and modified filled outlines, an overlay,
and enlarged views of the changed boundaries. Label colors, dimensions and units.
Highlight added or removed segments explicitly. Verify that unchanged regions
really coincide, rather than assuming the operation only affects the selection.
Export a readable PNG plus SVG or PDF when the user needs to zoom or share it.
Native CST screenshots establish context but may be too coarse to show a small
change in a large assembly.

## Restricted analytical SAT route for rotational parts

This route worked on an axisymmetric part exported by CST 2024.4 as ACIS 33 SAT.
It is a specialized alternative when all faces are analytical surfaces of
revolution. SAT layout is version-specific; inspect the actual header/records
and reject unsupported geometry. It is not a general-purpose CAD parser.

1. Confirm the rotation axis, center, coordinate units, and SAT encoding. Validate
   that every relevant face belongs to that same rotational geometry. Do not
   assume global Y or millimetres for another project.
2. Parse entity records by their terminating `#`, retaining `$` references. The
   native export can contain wrapped records and encoded shape-name attributes.
3. Follow each face's loops and coedges to its actual bounding edges. In the
   tested simple ring, each face had two full circular bounds. Confirm circularity
   and full angular coverage instead of assuming every ellipse is a circle.
4. For a trimmed toroidal face about Y, the meridian has
   `r(u) = R + a*cos(u)`, `y(u) = yc + a*sin(u)`, where `R` is the major radius
   and `a` is the minor radius. Use the face's actual trimmed parameter interval,
   axis orientation, and circular bounds. Other axis orientations require the
   corresponding coordinate transformation.
5. For coaxial planar annuli, cylinders and cones bounded by two circles, the
   meridian is the line segment between those two circle positions. Include newly
   added cylindrical or conical faces even when their length is very small.
6. Check every reconstructed segment endpoint against its CAD edge. Connect
   segments using the actual edge/face adjacency, not nearest-point guesses or
   invented gap-filling lines. Validate closed loops and account for all faces.
7. For a simple closed radial profile, compare the volume of revolution with
   CST's volume. A useful check is `V = abs(pi * integral(r^2 dy))` along the
   oriented boundary. Include inner loops with their correct orientation when
   present. This check does not establish smoothness or electromagnetic accuracy.

Do not treat unsupported splines, partial circumferential faces, non-axisymmetric
features, or disconnected contours as if they fit this method. Use a native
section or a real CAD intersection for those cases. Do not edit the SAT text to
change a surface radius: trimmed curves, topology and adjoining surfaces must
also remain consistent, which is a CAD-kernel operation.

When comparing face exports, face IDs and analytical surface definitions serve
different purposes. Several separated annular faces may share the exact same
plane equation. Compare their trimmed bounds/profile and topology as well as
their underlying surface. Recheck correspondence after an edit changes IDs.

## The offset failure to recognize

A local offset of a toroidal rounded tip can preserve its angular span and move
the arc outward while leaving neighboring fillets in place. CST may close the
body by inserting two short connector faces. The measured tip radius is then
correct and rebuild/save/reopen can pass, yet the joins have new sharp corners.
The full section reveals what a plot of only the enlarged torus would hide.

If the design calls for a smooth larger tip, use a tangent-continuous construction:
adjust adjacent fillets or boundaries together, rebuild an explicit profile, or
revise the original CAD as appropriate. Verify the new joins rather than assuming
a blend option fixed them. Do not silently repair beyond the user's requested
geometry; present the actual trial and its limitation when design intent is still
unspecified.

## Assembly and physics handoff

Before inserting the verified part into the full assembly, trace its original
boolean operations and update affected cavity/domain geometry. Check which of
the repeated instances should change, their transforms, materials, clearances,
and port/face selections. Confirm that no stale results are described as results
of the new geometry.

Do not infer the solver from the component's name. A ring in a time-domain project
does not become an LF MStatic model because the available bridge runs MStatic.
Keep the geometry deliverable and any later solve as separately reported stages.
