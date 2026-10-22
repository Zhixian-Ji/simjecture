# Frankfurt FEOS interface

Read this when operating `feos-16.7`. The capability runs workspace Python
programs with `FEOS_QUERY` pointing to `bin/feos-query` and `FEOS_DATA` pointing
to `share/feos`. Runtime identity binds the executable, build record, upstream
material database and Thomas–Fermi interpolation data.

Place these files in the experiment working directory:

- `FEOS_TF-Table_1197.dat`: copy from `FEOS_DATA`; the installer pins its hash.
- `FEOS_Material-DB.dat`: the explicitly selected material database. An upstream
  copy is in `FEOS_DATA`. For custom materials, declare the database as an input
  and retain the empirical parameter sources. Verify that the requested material
  is present; the archive does not include every material discussed in the paper.

Single-state query:

```text
feos-query MATERIAL_ID RHO_G_CM3 TEMPERATURE_EV OUTPUT.json
```

Batch query:

```text
feos-query --batch MATERIAL_ID STATES.csv OUTPUT.csv
```

The input CSV starts with `rho_g_cm3,temperature_eV`, followed by positive,
finite density/temperature pairs. The output preserves row order and adds
total, corrected electronic, ionic and uncorrected Thomas–Fermi pressure,
specific internal energy, entropy and free energy, plus the mean charge state.
Verify output row count and retain process diagnostics.

Pressure is in dyn/cm², specific energies in erg/g, density in g/cm³ and
temperature in eV. Entropy and temperature derivatives use **eV**, not kelvin:
entropy and `dE/dT_eV` are in erg/(g eV). Divide by 11604.51812 to convert
these to erg/(g K). The native free-energy identity is `E = F + T_eV S`.
Component energies and free energies retain the upstream reference offsets;
do not silently replace those offsets during table conversion.

The corrected electronic component includes the bonding correction. Reconstruct
a homogeneous two-temperature state using the electronic contribution at `Te`
and the ionic contribution at `Ti`. Store its assumptions and tested domain.
The adapter supplies neither an electron–ion equilibration rate nor radiation EOS.

The independently authored adapter calls `FEOS_Get_EOS_All` exclusively. In the
16.7 source, the task-selecting `FEOS_Get_EOS` switch lacks `break` statements,
so it must not be used to retrieve a component. Some upstream errors call
`exit(0)`. An exit guard changes premature termination into a nonzero failure;
finite-value and composition checks also run before successful completion.

This adapter does not enable Maxwell construction or the optional soft-sphere
cold-curve replacement. Expanded low-temperature states can therefore be
metastable or unstable. Preserve negative pressures and numerical failures in
diagnostics; exclude them from any material domain that requires stable states.
Positive heat capacity alone does not establish physical validity.

Source: Faik, Tauschwitz and Iosilevskiy, *Computer Physics Communications* 227
(2018), 117–125, [doi:10.1016/j.cpc.2018.01.008](https://doi.org/10.1016/j.cpc.2018.01.008).
The source archive includes the 16.7 manual. Its original design range is
`1e-7 <= rho/rho_ref <= 1e6` and `1e-4 <= T_eV <= 1e6`; these are not a
material-specific accuracy guarantee. Lower numerical floors in the code do
not justify extrapolation beyond the assessed physical domain.
