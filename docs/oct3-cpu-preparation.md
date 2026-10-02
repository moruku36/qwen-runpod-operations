# October 3 CPU preparation and handoff

Owner approval updated October 2: October 3 only, one new Pod, maximum 120 minutes,
maximum cumulative $10 including storage/taxes/fees, existing balance only. Start
time is unset. No Pod/API action, model-weight download or paid call occurred in
this preparation. Before launch, recheck live price, availability, Billing,
resources, transport, fixture readiness and private export destination. Both
October 2 Pods/volumes were deleted according to the owner's later confirmation;
rebuild `/workspace` from scratch. The October 2 historical report is unchanged.

## Evidence and scope

Base operations commit: `a3f5f6835d9b4d3f1d20de4ac1bcef075f2c32fc`.
Upstream source was read at `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`.
No AGENTS.md, .agents or repository-local skills were present in the checkout.
Original notebook blob, llama.cpp commit and both Qwen model pins remain unchanged.
The Linux dependency lock is unchanged; LF checkout is enforced to prevent Windows
CRLF from changing its byte hash. Expected SHA256:
`d9fb03b78fed58478ebe7e67023d93d331e5b9b8ffcba613f267d4599efdc8d0`.

The new CPU tests use standard library fakes, a real isolated CPython 3.11 venv,
real local process deadlines, corrupted tar bundles and a local log round trip.
They do not prove CUDA, full application UI or real model features. Existing
pytest is available only in CPython 3.10 here; no dependencies were installed.
Test counts, interpreter versions and exits are in the local preparation evidence.
Reviewed baseline b324fb8: 35 standard-library tests passed on isolated CPython3.11.9,
and existing pytest (including those tests) had 96 passed, 14 skipped on CPython3.10.
The overlapping tests are not 131 distinct successes. Review corrections add Linux
negative cases and diagnostic cancellation tests; their fresh results are retained
with the new execution SHA. Skipped remains skipped. A separate end-to-end CPU
control/export rehearsal is retained locally.
Review-fix suite: 49 collected standard-library tests, 45 pass/4 POSIX skips on
Windows CPython3.11 and 49 pass on existing WSL CPython3.12; the existing pytest
suite includes these cases. The production Gradio application still has not been
rehearsed with the Linux CPython3.11 lock.
Early test attempts failed on Windows temp permissions, text encoding and POSIX
assumptions; the final run uses a fresh workspace temp directory and UTF-8 mode.
POSIX-only checks and unavailable upstream/app dependencies are explicitly skipped.

## What changed

- Kernel registration exit and kernelspec interpreter verification block progression.
  Notebook kernel selection still needs an owner observation.
- QMC2 events/artifacts bind a fresh run ID. Strict receipt requires exact events,
  filenames and full SHA256. Empty/partial logs are never silently treated as full
  success. Partial transport can still supply a complete specified receipt; its
  transport observation remains recorded separately.
- Stop API EXITED is recorded separately from console/system-log corroboration and
  pending Billing. A residual API rate value is not a settled charge.
- Local bootstrap checks exact clean commit, lock, cost quote, approval date, ordered
  stages, fresh root, licensed fixture hashes and explicit deadlines. Checkpoints
  bind the packet hash. CANCEL/deadline kills the local command; Linux process
  groups include child pip/build/model processes. **This does not stop Pod billing.**
  External owner initiates Stop by T+110 (or earlier budget deadline) and verifies
  by T+120. No resource creation, restart, deletion or external agent exists in the
  new controller. Windows cancellation targets only the controller's started PID
  tree; a venv launcher child was caught by the deadline test. Existing WSL tests
  cover Linux stragglers after leader exit, TERM-ignoring children, group-disappearance
  races and a blocked trial's inner deadline/diagnostic export on Python3.12.
  The target Python3.11 full UI rehearsal remains a separate missing gate.
- Build/download times are separate; missing server or incomplete model records fail.
- Warm samples retain errors, finish reasons and chunk counts in report JSON and
  are checkpointed after each sample on disk. Unknown/incomplete finish cannot pass
  performance. Thresholds remain proposed usability criteria. Stream chunks are
  not exact output tokens.
- Feature adapters explicitly attempt tokenizer-measured >=28672 input at context32768,
  reload/effective-context/truncation/OOM evidence, synthetic image facts, public
  search retrieval/linkage, CPU small ASR, and independently reread synthetic
  history. Exceptions are fail; unattempted features are skipped with reasons.
  UI input-to-display remains an owner check. Feature results are embedded in the
  validated report; bundle verification checks exact members/counts, all hashes,
  run ID and requested acceptance checks, rejecting unsafe/duplicate/corrupt members.
- The history DB now belongs to the run root, preserving isolation from other tasks.
  Research agents are off, including functional turns. Search explicitly chooses
  the keyless DuckDuckGo provider. Its pinned upstream DDGS adapter can transmit
  the public fixture query through engine fallback (DDG/Bing/Brave/Google/Yahoo/
  Mojeek); no private query or paid API provider is used.

## Handoff commands after readiness and owner allocation

Copy the repository at the final reviewed execution SHA and complete
`examples/oct3-packet.template.json` with actual allocation/deadlines, live all-in
quote, private retrieval path and licensed speech/transcript hashes. The template
is deliberately invalid while these values are unset. Keep the packet outside the
checkout to preserve a clean tree. Record both UTC and JST externally. Review pip
progress at 10 minutes; G2 incomplete at 20 minutes requires the external stop
decision. Do not improvise a fixed 240-second pip timeout.

The packet now separates `test_deadline` (no later than T+100), `export_deadline`
(no later than T+110, at least 30 seconds after work ends), and `stop_deadline`
(Stop verification by T+120). An earlier budget deadline wins. Linux main-thread
work receives SIGALRM at the inner deadline, then creates a diagnostic bundle
under the separate outer bound. Warm loops check cancellation before each sample,
and completed warm/feature/UI observations survive interruption. Cancellation gives
up to 30 seconds of bounded signal grace within the outer time remaining. No new
CUDA metadata subprocesses run during failure diagnostics. External Stop remains
mandatory; these process bounds never establish billing control.

Failed/cancelled runs and interrupted in-flight trials are terminal: use a new
run ID and root. Even direct stage invocation cannot emit a second same-name trial
bundle for an existing run. Do not resume an old checkpoint to retry inference.
Kernelspec verification compares the intended venv path and prefix, rather than
resolving its Python symlink to the OS binary. UI receipts are snapshotted before
feature/context changes; malformed receipts produce a fail row without aborting export.

```sh
python3 scripts/retry_run.py /workspace/private-packet.json --validate
python3 scripts/retry_run.py /workspace/private-packet.json --execute-local
```

The first execution stops with exit2 after selftest until an **off-Pod** receipt is
read back. Use the actual selftest hash (probe text is unchanged), fresh run ID and
the existing read-only logs route:

```sh
python scripts/read_pod_logs.py NEW_POD_ID --run-id FRESH_RUN_ID \
  --expect-event 'selftest:hello from the pod' \
  --expect-file 'selftest.txt=467e5a2b0ffc26ed3a065a9b8dbfbff233d6c978814172af05e215a2df910786' \
  --out PRIVATE_OFF_POD_DIRECTORY
```

Return the verified receipt file to `ROOT/selftest-receipt.json` using the existing
human Jupyter action; rerun the same execute-local command. This invokes network,
source, venv, build, 8k cold/10 warm and explicit feature adapters under the test
deadline. A failure stops progression. `ROOT/CANCEL` cancels local work. The Pod
must still be stopped externally even after exit124 or failure.

After warm samples, bootstrap allows up to two minutes for the owner to check the
authenticated Japanese UI at 8k, before the 32k reload. Write `ROOT/ui-check.json`
only after real observation: `{"run_id":"FRESH_RUN_ID","observer":"owner",
"input_to_display":true,"language":"ja","ctx":8192}`. No chat text or login
values belong in this receipt. No receipt means skipped; a stale/negative receipt
fails. Record notebook kernel selection separately as an owner observation.

Receive the emitted final report bundle using exact expected filename/hash and
run ID. Run `report.verify_bundle(path, expected_run_id=..., required_checks=...)`
outside the Pod. A receipt proves export completeness, not feature success. Keep
`report_exported` skipped in the producer report until a separate off-Pod receipt
confirms it; do not rewrite the hashed original bundle to claim its own export.
Warm/feature/UI/stop checks must be evaluated individually; stage=ok is insufficient.

Full handoff count is not verified. CPU tests prove two bootstrap invocations
separated by a receipt gate. Upload/download, UI verification, notebook selection,
external stop and billing remain human actions. One-paste autonomy is not promised.

## Remaining no-go items

Follow-up after `fe32f7c`: the Linux signal guard encloses work, export and owned-app
cleanup. Work TERM/INT triggers diagnostics; export TERM/INT is deferred until the
immutable export deadline, without extending the controller's hard limit. Direct
manual calls without a packet get a 30-second export bound after work. Cleanup is
always attempted for a returned app, including KeyboardInterrupt/SystemExit and
export errors; SIGKILL/uninterruptible cleanup cannot guarantee completion. Controller
cleanup grace is capped by remaining outer time (post-KILL observation at most two
seconds and leader reap at most three seconds). Existing UI receipts are recovered
on early interruption, missing build metadata is unavailable, and refused reruns
append a separate refusal event without changing prior status or bundles.

1. Full Gradio/auth/application CPU rehearsal in an isolated CPython3.11 environment
   with reviewed compatible dependencies. Current lock targets Linux x86_64
   manylinux_2_35, not Windows. No ad-hoc system packages or security changes.
2. Select and document the speech fixture's use assumptions and its paths/hashes.
   A supplementary offline Windows standard-voice fixture and known transcript
   were generated outside the reviewed candidate; no personal voice or paid API.
   ASR source metadata only
   was read from the public Hugging Face model API; fixed model revision is
   `Systran/faster-whisper-small@536b0662742c02347bc0e980a01041f333bce120`.
   No ASR or Qwen weights have been fetched. Audio decoding/transcription, CUDA,
   native llama API fields and real functional adapters remain runtime-unverified.
3. Private export destination, completed immutable launch packet, initial/emergency
   owner presence and next-day live quote/readiness checks. Existing Claude cloud
   session reply round trip is still unverified; no live third-party agent was called.
4. Final review/pin and deployment of this local branch. Nothing was pushed/merged.

The new diagnostic reserve is tested with CPU fakes; it is not guaranteed recovery
from SIGKILL, kernel OOM, uninterruptible I/O or failed export. If those prevent finalization,
local checkpoint/raw sample files may exist but a final report bundle may be absent. Mark export incomplete,
retrieve available allowlisted diagnostics if possible, and Stop first if required
by cost/time. Do not delete resources or relabel missing evidence pass.
