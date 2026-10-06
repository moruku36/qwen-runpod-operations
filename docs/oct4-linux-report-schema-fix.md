# Linux CPU report schema correction

Predecessor source: 195e2a72657f41a997b9b406112756033fc3c540.
The first executed Linux validation exposed missing report allowlist entries for
evaluation_profile and feature_scope. This rejected normal and diagnostic export
before finalization. The original handoff is retained unchanged.

This revision adds only those typed, bounded schema fields. Chat-only reports
require the exact five not_requested features and skipped checks. Unknown
profiles, unknown keys, sensitive-looking strings and contradictory feature
claims remain rejected. Four CPU regression tests cover these boundaries.
No deadline, cancellation, process-group, budget, model pin or receipt checks
are relaxed. Python 3.11 and pytest setup was separately approved for the Linux
workspace; it is not permission to install production/GPU dependencies.

Validation must use the regenerated pinned source bundle and candidate manifest.
Actual GPU load, performance, UI and provider Stop are not exercised here.
No resource operation, payment, model download, remote publication or launch
authorization is included.
