# Experimental solver development: acceptance

Validated on 2026-10-12 for the unreleased development branch. The existing
remote research campaign was not updated, restarted or steered during this change.

## Behavior

New minimal studies allow private experimental solver forks by default. The
study brief, monitor launch dialog, CLI and agent drafting API expose the choice.
An explicit disabled choice survives drafting, launch, resume and continuation
preparation. The permission is immutable within a study; a new continuation phase
can declare a different choice. Older studies without the field retain their saved
instructions. Restricted public trials retain the host's supplied-solver policy.

Registration preserves existing instrument identities and can record the parent
instrument, numerical/physical/diagnostic change type, rationale, preserved
assumptions, validation plan and completed build receipts. Workers, directors and
reviewers see the same policy and provenance. The monitor displays method approvals
with their scopes rather than presenting a benchmark as validation of an entire
solver. A newly registered evidence instrument requires an approved production
method, including in studies whose original instruments did not require review.

This is a scientific permission and managed-registration contract. It does not
change provider-native tools or operating-system access. Turning it off preserves
ordinary experiment and analysis scripting. Reference protection still depends
on the operator's filesystem permissions; the checkbox does not make an existing
writable installation read-only.

## Validation

| Check | Result |
|---|---|
| Focused backend suite | 184 passed, 91.29 seconds |
| Playwright browser suite | 9 passed, 32.07 seconds |
| Python lint, JavaScript syntax and whitespace | Passed |
| Sphinx documentation build with warnings treated as errors | Passed |

Backend coverage includes new-study defaults, strict Boolean validation,
immutable choices and provenance, legacy resume without inferred permission,
production-method review, remote registration, role instructions, continuation
inheritance, resource repairs and restricted hosted permissions. Browser coverage
checks desktop and mobile monitor layouts, build provenance links, instrument
readiness labels, the default-on checkbox, and a disabled choice persisted into
the launched contract. The workspace handoff uses the local execution path with a
fake model provider.

The final backend run covered `test_solver_development`, `test_study`,
`test_study_interface`, `test_research_director`, `test_research_oversight`,
`test_research_service`, `test_research_continuation`,
`test_research_resource_repairs`, `test_research_remote_registration`,
`test_workspace`, `test_public_workspace` and `test_public_native`. Browser checks
covered `test_research_progress_browser` and the workspace setup/chat/files/study
handoff test.

An earlier combined backend/browser invocation exited with SIGILL during Python
garbage collection. Its stack did not establish the cause. The final suites ran
in separate processes and passed; this does not establish that the combined-process
environment issue is resolved. No full-suite or paid-model validation is claimed.

These checks establish harness behavior, not the numerical correctness of an
experimental solver repair or its scientific conclusions. See the
[permission and qualification guide](../how-to/minimal-oversight.md#solver-development-permission)
and the earlier [large-build and remote-registration repair](large-build-remote-registration.md).
