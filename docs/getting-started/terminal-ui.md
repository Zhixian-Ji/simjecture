# Terminal interface (maintenance mode)

The [Web interface](web-interface.md) is the primary interactive client. The optional
Textual dashboard remains available for SSH and browserless use. It launches
**minimal** studies with a native CLI agent and can inspect historical research
records. Classical/MVP, DSH, structured and frontier launches are removed; see
[migration](../how-to/minimal-migration.md) before upgrading an older running study.

## Status and watch

These commands work with the core package and do not require Textual:

```bash
simjecture status artifacts/my-study
simjecture status artifacts/my-study --json
simjecture watch artifacts/my-study
simjecture watch artifacts/my-study --jsonl
```

`status` prints a durable snapshot. `watch` follows progress; Ctrl-C stops the
viewer without cancelling the study. The view reports recorded experiments,
review state, model usage and time remaining. Unknown usage is not zero, and a
missing terminal report does not imply that a process is running.

Minimal studies use `research.json`, experiment receipts and
`research_report.json`. Archived classical records retain their original manifests,
ledgers, transcripts and reports. Viewing either format does not rewrite it or
change its scientific conclusions.

## Launch and inspect

```bash
uv sync --extra tui
uv run simjecture tui
uv run simjecture tui artifacts/my-study
```

The home screen lists recent studies. Its new-study form asks for the hypothesis,
optional instructions, agent backend and model, execution backend, output directory,
wall budget and agent watchdog. Minimal is the only workflow; legacy-only memory
and workspace fields are no longer shown. Review the contract before launching.

The hypothesis and instructions are written to files, and the child process receives
file paths as arguments. No shell command is built from hypothesis text. The
saved `study-launch.json` records the contract, and `controller.log` captures output.
For the built-in API agent, use the browser workspace or
[headless launcher](../how-to/headless-studies.md).

The dashboard displays hypotheses, validation records and linked evidence. The
artifact browser and audit ledger remain available for older records. Model prose
is never treated as scientific acceptance.

## Pause, resume and cancellation

```bash
simjecture pause artifacts/my-study
simjecture resume artifacts/my-study --detach
```

Pause and cancellation require a verified process identity. Minimal studies record
control requests in the supervisor directory. Resume preserves the saved launch
contract and original absolute deadline; pausing does not replenish the budget.
Use [budget extensions](../how-to/extend-study.md) for an explicit increase.

Older classical, DSH, structured and frontier studies cannot resume in this release.
The dashboard reports migration guidance. Preserve the original environment if you
need to continue those runs. Cancelling a verified process remains available.

If Textual is missing, `simjecture tui` prints the installation command. Native
agent credentials and numerical runtimes are configured separately; see
[installation](installation.md) and [minimal research](../how-to/research-service.md).
