# Material profiles and table export

Read this for `material-eos-0.1`, when constructing a table from a named model
or implementing a published material recipe. Use `feos-16.7` for direct native
FEOS state queries. Both belong to the EOS skill; neither is opacity evidence.

## Launch

The installed runtime exposes `MATERIAL_EOS_BUILD`, `FEOS_QUERY`, `FEOS_DATA`,
and `FEOS_TF_LIBRARY`. Launch the builder with the capability's Python, not an
unrelated host interpreter:

```python
import os
import subprocess
import sys

subprocess.run([sys.executable, os.environ["MATERIAL_EOS_BUILD"], "profiles"], check=True)
subprocess.run([
    sys.executable, os.environ["MATERIAL_EOS_BUILD"], "build",
    "--profile", "model.json", "--grid", "grid.json", "--output", "eos-output",
], check=True)
```

The wrapper program is the `source` of a normal `lab.run` with the advertised
`material-eos-0.1` capability. Keep profile, grid, database, script and outputs
inside the experiment workspace and record them in the prospective method.
Start with exploration while choosing a physical model or repairing the interface.

`grid.json` contains strictly increasing positive arrays named `density_g_cm3`
and `temperature_K`. Choose their domain and resolution from the active task;
the tool supplies no default scientific domain. The output directory must be new.

The `native-feos` profile additionally requires `--database material.dat` and
`--material-id ID`. It uses the native Cowan ion and bonding model from the
declared database. Copy the upstream database from `FEOS_DATA` only when that
is the intended material input; custom entries need their own parameter sources.
The current exporter supports single-element entries and rejects mixtures.

## Profiles are model identities

`profiles` lists the installed recipes and their scientific status. Read the
selected profile's `sources`, `parameters`, `modifications`, and `limitations`.
The package stores paper-specific parameters outside the general skill.
The source paper, the native code, and a reconstruction with repaired constraints
are different model identities even when they share an electronic backend.

A supplied JSON profile has schema version `1`, with fields `id`, `version`,
`engine`, `scientific_status`, `sources`, `parameters`, `modifications`, and
`limitations`. Copy a supported recipe before editing it. A new engine requires
implementation and tests; changing a profile name cannot create new physics.
The current paper engine rejects changing its element identity. For a new
element, commission an explicit native material database or implement an
appropriate new model instead of transferring another element's phonon fit.

When adapting a paper, distinguish reported coefficients, fitted quantities,
derived reference constraints, and additional choices made in the reconstruction.
Retain a failed literal reconstruction when it explains a change, and assign
the revised model a separate version. A bibliographic citation alone does not
establish a parameter set's validity over the requested state domain.

## Component accounting

Native FEOS output uses its corrected electron/ion split; the electron term
already includes bonding. The paper engine uses raw TF thermal increments
relative to the same-density zero-temperature TF state, with the cold remainder
assigned to ions. The output records the actual partition. Do not compare these
electron fields as if they had identical definitions.

The builder uses constant component energy offsets to make exported energies
positive and records the exact offsets in J/kg. They do not change pressure or
heat capacity. Include the offsets when initializing a consuming code by energy.
A temperature-based initialization must still use the table's actual domain.
The partition of a single-temperature free energy does not independently
validate a nonequilibrium two-temperature application.

## Outputs and checks

The output includes `table.cn4`, SI component arrays, a profile snapshot,
`metadata.json`, a parameter fragment, and checksums. IONMIX4 stores temperature
in eV, ion number density in cm^-3, pressure in J/cm^3, and energy in J/g.
The builder handles these conversions. Do not convert the output a second time.

The default export rejects failed regular-domain checks. `--allow-unstable`
retains such states only as an explicit research export, with failure counts
and per-node flags. It is not a declaration that a consuming solver can evolve
them physically.

The format has six significant digits. If it creates flat component-energy
intervals, use a coarser grid or explicitly select `--coarsen-temperature-grid`.
The latter removes only temperature columns needed for monotonic serialization,
records them, preserves the endpoints, and rejects physical/non-numerical
energy nonmonotonicity instead of hiding it. It does not perform a phase-equilibrium
construction.

Use `eos_useLogTables = .false.` for the supplied linear-data contract. Zero
radiation groups mean **no opacity**: do not register the file as an opacity table.
Adapt the parameter fragment's species name to an already compiled solver species.

Keep these assessments separate: execution/readback, thermodynamic consistency,
agreement with independent material data, the actual consumer's reader and
interpolator, and a full simulation. The builder records the first checks; it
does not claim that the later assessments occurred.
