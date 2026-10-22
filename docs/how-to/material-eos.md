# Build material EOS tables

Simjecture exposes two complementary Research tools. **Frankfurt FEOS** evaluates
native electron/ion thermodynamics. **Material EOS builder** constructs a table
from an explicit model profile and exports an EOS-only FLASH IONMIX4 file. The
EOS skill guides model selection, parameter provenance and validation for both.
Neither tool calculates opacity.

## Install and inspect the profiles

From a source checkout:

```bash
uv run simjecture install material-eos
uv run simjecture doctor --profile material-eos
.runtime/material-eos-0.1/bin/python \
  .runtime/material-eos-0.1/bin/material-eos-builder profiles
```

The builder has an independent managed runtime containing FEOS 16.7, NumPy,
SciPy and the model code. Installation verifies the upstream archive and runs
an aluminium interface probe through the execution sandbox. This establishes
that the interface works, not that a material model is physically qualified.
The separate direct-query tool remains available as `simjecture install feos`.

Two profiles are initially included:

| Profile | Model and status |
| --- | --- |
| `native-feos` | Native FEOS QEOS, using an explicitly supplied material database and ID. Material validity depends on those inputs and the chosen state domain. |
| `tin-ding-2026-reconstruction` | Research reconstruction of the tin paper, with raw FEOS Thomas–Fermi electrons and separately implemented ionic and bonding terms. Reference calibration repairs the inconsistent low-density parameters; it is not an exact reproduction of the printed coefficients. |

The tin recipe records the [source paper](https://doi.org/10.1063/5.0350372),
all parameters, reconstruction choices and limitations. Paper-specific data
live in versioned profiles outside the general skill. The code does not include
the paper PDF or third-party FEOS source/data in the source distribution.

## Choose inputs and build

Provide a JSON grid containing strictly increasing positive arrays
`density_g_cm3` and `temperature_K`, with at least two values in each.
Select its range and resolution for the intended simulation. The builder
does not choose a scientific domain automatically.

For the native model, supply a material database and a four-digit material ID:

```bash
.runtime/material-eos-0.1/bin/python \
  .runtime/material-eos-0.1/bin/material-eos-builder build \
  --profile native-feos --database material.dat --material-id 1234 \
  --grid grid.json --output native-eos-output
```

Here `1234` is a placeholder for an entry in your database, not an included
material prescription. The exporter checks the selected entry and currently
supports pure elements only. Keep material inputs and their parameter sources
with the experiment record.

For the tin reconstruction:

```bash
.runtime/material-eos-0.1/bin/python \
  .runtime/material-eos-0.1/bin/material-eos-builder build \
  --profile tin-ding-2026-reconstruction \
  --grid grid.json --output tin-eos-output
```

Output directories must be new. A custom JSON profile can replace the profile
name, but it must provide the complete supported schema. The tin engine rejects
relabeling its element. Tungsten requires its own sourced native material entry
or a separately implemented and calibrated model; changing atomic number and
mass does not transfer tin's phonon or bonding parameters.

Inside an agent experiment, use the advertised `material-eos-0.1` capability
and launch `MATERIAL_EOS_BUILD` with that capability's Python. The EOS skill's
`references/material-models.md` documents this execution route.

## Interpret and validate the result

Each successful build produces:

- `table.cn4`: fixed-width IONMIX4 thermodynamics with zero radiation groups.
- `table_si.npz`: component arrays and per-node diagnostic flags in documented SI units.
- `profile.json` and `metadata.json`: model, runtime and input identities,
  calibration, units, energy shifts, diagnostics and serialization errors.
- `flash.par.fragment` and `SHA256SUMS`: a species parameter example and artifact hashes.

Native FEOS assigns bonding to its corrected electron contribution. The tin
profile uses thermal TF electron increments relative to the same-density
zero-temperature state and assigns the cold remainder to ions. The metadata
records this distinction. Constant component energy shifts make the exported
energies positive; account for these shifts when initializing by energy.
This component partition does not independently qualify two-temperature physics.

The default rejects nonfinite quantities, invalid ionization, nonpositive bulk
modulus or component heat capacity, negative component pressure, and energy
nonmonotonicity. An explicit `--allow-unstable` permits a research export with
recorded domain failures, but cannot bypass finite-value or ionization checks.
This option does not establish that FLASH can evolve those states physically.

IONMIX4's finite precision can flatten adjacent energy values. The optional
`--coarsen-temperature-grid` removes temperature columns only when the model's
energies already increase strictly; it preserves endpoints and records every
removed temperature. It cannot repair physical energy nonmonotonicity.

Generation and format readback are distinct from FLASH validation. Test the
actual target FLASH reader, interpolation, energy-to-temperature inversion and
solver over the required domain. The parameter fragment must use a species
compiled into your application, and the table supplies no radiative opacity.
Compare the selected model against independent material data before using it
for scientific conclusions.
