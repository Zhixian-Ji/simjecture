# Local deployment

This reference is for the operator installing or repairing a local EOS runtime.
It is not an autonomous campaign procedure.

## License boundary

atoMEC, Singularity-EOS, and M-ANEOS are optional packages. The installer clones
and builds pinned upstream revisions into a Git-ignored runtime. That does not
relicense those packages or copy them into the Simjecture source tree. Review
each upstream license before installing.

| Package | Upstream | Typical license |
|---|---|---|
| atoMEC | https://github.com/atomec-project/atoMEC | BSD-3-Clause |
| Singularity-EOS | https://github.com/lanl/singularity-eos | BSD-3-Clause |
| M-ANEOS | https://github.com/isale-code/M-ANEOS | MIT |
| Frankfurt FEOS | https://data.mendeley.com/datasets/6vjsv6v48p/1 | GPL-3.0-or-later |

Bundled third-party numerical libraries retain their own notices. The public
Simjecture skill and capability metadata must remain independently written.

## Install with Simjecture

Use Install in Research tools, or run these commands from the installed Simjecture workspace:

```bash
uv run simjecture install atomec
uv run simjecture install singularity-eos
uv run simjecture install m-aneos
uv run simjecture install feos
```

Repeating a command against a healthy runtime performs no installation. An
interrupted managed installation resumes when Install is clicked again; unrelated
unmanaged directories are never overwritten. `--dry-run` prints the bootstrap command without writing a runtime.
`--source` may point at an already cloned tree whose `HEAD` matches the pinned
revision; otherwise the bootstrap clones it. `--jobs` is passed to compilation
where relevant.

The installer provisions Python, Git and the required C++/Fortran compilers and
libraries into managed environments automatically. No system compiler or sudo
is needed. Compatible package downloads are shared through a common cache.
Failed readiness outputs remain in `.runtime/deployment/preflights/`.

Then verify:

```bash
uv run simjecture doctor --profile atomec
uv run simjecture doctor --profile singularity-eos
uv run simjecture doctor --profile m-aneos
uv run simjecture doctor --profile feos
```

The doctor probes are permanently non-evidentiary interface checks.

## Runtime layout

The installer writes:

```text
.runtime/<capability>/
  bin/python
  bin/eos-query          # Singularity-EOS
  bin/maneos-query       # M-ANEOS
  bin/feos-query         # Frankfurt FEOS
  share/build-record.json
  share/preflight/ANEOS.INPUT   # M-ANEOS, upstream example deck
```

`bin/python` is the capability executable. Sibling scientific binaries are
declared in `identity_files` and hashed at discovery.

## What the installer builds

- **atoMEC 1.4.0:** a managed Python environment with libxc 6.2.2 and the pinned
  atoMEC revision. The probe is a helium ion-sphere SCF.
- **Singularity-EOS 1.12.1:** a query driver compiled against the pinned source
  for `IdealGas` and `IdealElectrons`. The probe evaluates `IdealGas` only.
- **M-ANEOS 1.0:** `libaneos.a`, the query driver, and the upstream example
  `ANEOS.INPUT` as a non-evidentiary preflight deck. That deck does not
  qualify other materials.
- **Frankfurt FEOS 16.7:** the public archive identified by
  `10.17632/6vjsv6v48p.1`, verified against SHA256
  `01c2e0d9e3d5c787418405bf9dd3bc5beec28566d4bdb7c53cffbabf756b5eb4`,
  an unmodified upstream library, independently authored query adapter, TF data,
  material database, license and manual. `--source` accepts an extracted copy
  of that archive; the installer verifies all source-file hashes rather than
  requiring Git metadata. It builds in a separate directory and records compiler
  and adapter identity. The probe uses the upstream aluminium deck only.
  No material calibration or opacity data is implied by installation.

Manual layouts remain valid if they match the capability descriptor. Rebuild
and requalify after compiler, Python, or upstream-revision changes.
