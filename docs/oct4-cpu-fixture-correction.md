# Oct 4 candidate CPU fixture correction

The reported v1 run covered 45 helper tests (3 errors) and 63 source discovery
items (3 failures, 12 errors, 7 skips). The original candidate and hash inventory
remain retained as the exact failed input. The log includes the initial manifest
hash but no printed final hash; no post-run hash equality is inferred.

Corrections in this local v2 candidate are test preparation changes:

- The positive absolute-stop fixture starts at 13:20, leaving 60 work minutes.
  A 13:30 start still fails the production 60-minute work floor.
- Retry command tests use an explicit Oct 3 fake clock. The production real-clock
  deadline checks are unchanged. A separate assertion still prevents an expired
  stage from running and records cancellation.
- The outer trial allowance is 4800 seconds, from 01:05 to 02:25 UTC. The old
  6300-second expectation predated the current packet milestones. The test also
  asserts that the command allowance extends past the 02:20 work deadline.
- CPU fixtures import production modules from a fresh local pinned-bundle clone
  made by the running user. Actual commit, clean-tree and lock checks remain.
  No safe.directory, ACL or identity change is used. The v1 log's exit 128 did
  not capture stderr, so an ownership rejection is probable, not proven.
- The scoped source suite is the standard-library unittest module
  test_retry_cpu. test_runpod and test_stages are pytest function suites; they
  need pytest and its collection mechanism. Installing pytest would not make
  unittest execute those functions. Their regression status remains not_run.

No production source code changed. v2 has not been run: Python execution under
the current Codex identity was denied and retry/bypass is prohibited. Before a
new user-facing run command, an explicitly authorized CPU environment must run
verify_cpu.py with Python 3.11 and Git already available. A local clone avoids
the repository owner mismatch but does not solve the denied Python executable.
Linux-specific skips on Windows remain unverified, not passed.
