# Public data for the 48 hour FRC campaign

Read the [campaign report](../../../docs/research/frc-48-hour-campaign-20261014.md)
for the physical question, unsuccessful outcome, numerical findings and costs.
The campaign ran on 12–14 October 2026 with eight RTX 2080 Ti GPUs. Its
scientific hypothesis remains unresolved.

This bundle contains a deliberately limited export of the retained audit:

- `metrics.json`: configuration, outcome, role totals, coverage gaps, numerical
  findings, published pricing assumptions and source-summary hashes.
- `executions.csv`: one row per execution, with outcome, purpose, stage, audit
  group, receipt start/finish relative to launch, source duration and requested
  GPUs. Source duration is not the full receipt interval.
- `worker-requests.csv`: native worker usage increments and request lengths.
  Cached input is a subset of input; reasoning output is a subset of output.

The work-group labels follow the original audit's entrypoint/capability heuristic.
They do not count independent scientific trajectories. Requested GPU-hours sum
source duration multiplied by GPU allocation; they do not measure utilization.
Worker cumulative totals include final drafting. Other model usage is aggregated
once per thread in `metrics.json`; 39 threads have unknown consumption. Their
individual request lengths and cache-write counts are unavailable.

The export contains no host addresses, personal directories, prompts or native
session IDs. Raw receipts, source snapshots and checkpoint fields are outside
this bundle. The hashes identify retained private audit summaries; they do not
make those original records publicly retrievable.

Run from the repository root:

```bash
uv run --with matplotlib python scripts/plot_frc_campaign_report.py
```

Use `--no-plots` to check the tables and calculate API scenarios without
Matplotlib. Add `--node-hourly-usd RATE` for a whole-node rental assumption.
The scenarios use Standard API rates checked on 14 October 2026; the original
campaign used OAuth native Codex, so these are not its invoice. Reproducing
accounting arithmetic does not rerun or validate the plasma simulations.
