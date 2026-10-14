# Research feedback and evidence-packet repair, 2026-10-14

A long research campaign exposed two operational failures: repeated status checks
could conceal a stalled worker, and remote artifact inventories could overwhelm
review packets. The repair also makes independent report feedback available before
the finalization reserve and identifies gaps in native-agent token accounting.

## Changes

- Progress detection retains a bounded history of useful actions across narration.
  Status polling does not reset the stalled-progress counter. Recorded scientific
  progress and provisional command activity are reported separately.
- `lab.wait(...)` parks worker inference while awaiting experiments, reviews, report
  feedback or operator direction. Completed receipts and new steering wake the
  worker. Quiet waits use lightweight checks; the original deadline continues.
  The director can explicitly request report assessment and park the worker.
- `lab.assess_report(...)` freezes a report and its scientific snapshot for an
  independent assessment during research. Identical snapshots reuse the assessment.
  Acceptance of an unresolved report does not approve a scientific claim. The
  monitor shows requests, decisions and frozen reports; operator workspaces can
  request assessment through the same API.
- Review packets reference immutable, hashed artifact indexes instead of embedding
  every remote artifact. Reviewers can retrieve selected entries. Long console
  output is explicitly excerpted, with the complete receipt retained on disk.
  Routine report generation also uses the compact scientific snapshot.
- Native invocations record role, elapsed duration, cumulative usage scope and
  accounting coverage. Missing counters remain visible; interrupted worker usage
  can be recovered from later cumulative counters. An allowance is no longer
  displayed as measured runtime.

Scientific receipts, evidence stages, hypothesis approval rules and campaign
deadlines remain intact. See the [oversight guide](../how-to/minimal-oversight.md)
for the API and monitor controls.

## Archived-data replay

Replaying 268 late worker turns against an unchanged scientific record reduced
progress-counter resets from **95 to 2**. The retained resets represented the first
distinct useful action variants; the new detector subsequently identified 261
consecutive turns without progress. This is a deterministic replay, not a rerun
of the scientific campaign or a measurement of future token savings.

For one receipt containing **32,854 artifact entries**, its review projection
fell from **5,362,008 to 4,928 bytes**. The original receipt and inventory remain
available, and the failed experiment remains failed.

## Live native-agent checks

Two OAuth Codex trials used GPT-6.1 Sol low as worker and high as reviewer on a
numerical oscillator task. They exercised the real controller, recorded numerical
execution, independent report assessment and parked waiting.

| Original wall budget | Normal worker turns | Input tokens | Cached input subset | Output tokens | Observed behavior |
|---|---:|---:|---:|---:|---|
| 6 minutes | 3 | 357,693 | 307,712 | 4,786 | Early report accepted; worker parked |
| 8 minutes | 5 | 609,042 | 514,688 | 7,653 | Early report accepted; steering woke worker; revised report accepted; worker parked again |

Both trials reached their original deadline with the scientific question
unresolved and zero unreported native threads. These are operational checks,
not matched cost benchmarks.

Both trials also exposed a finalization defect: a reused snapshot compared Python
tuples against JSON lists and rejected a valid frozen report. Their final reports
remain recorded as **unreviewed**. The defect was fixed using JSON-stable identities;
the final focused regression covers reuse with retained failed experiments, and
the actual eight-minute trial's snapshot was successfully reused without changing
its report or packet hash. A further live finalization run has not been performed
after that correction.

## Verification

- Final focused run: **137 passed**, including waiting, report assessment,
  finalization, director, progress detection, receipt indexes, accounting and
  desktop/mobile browser controls.
- Broad run before the final snapshot correction: **1,393 passed, 5 skipped,
  1 failed**. The failure is the existing installed FLASH/MPI sandbox smoke
  problem, also recorded in [rc2 acceptance](0.6.2rc2-acceptance.md). The final
  focused run covers the subsequent lifecycle corrections.
- Python lint, JavaScript syntax and whitespace checks passed.

The original long scientific campaign was not restarted or altered by these
checks. No new physical conclusion or measured long-campaign spending reduction
is claimed.
