# Build-to-trial handoff correction

## Confirmed defect

The build and trial stages execute in separate Python processes. The earlier
build stage assigned QMC_LLAMA_BIN_DIR only in its own process. Trial read the
server path from build.json to request --version, but did not give that path to
the application. Upstream consequently searched its environment, legacy paths
and PATH. CPU process-boundary reproduction showed:

- a clean trial environment cannot find the built server and raises ModelLoadError
  before the server log is created;
- a stale environment or PATH can select a different executable;
- explicit artifact binding resolves and executes the intended server.

The received GPU diagnostic report is consistent with this defect: build/model
verification and UI/auth succeeded, while the first model load failed. The prior
Pod environment and raw exception were not recovered, so this code-level proof
must not be described as direct recovery of the Pod's exception message.

## Correction and related checks

- Build persists an explicit handoff schema, pinned llama commit and server SHA256.
- Trial accepts only a nonempty executable inside its current llama-bin root,
  matching that digest; it binds this exact server into application config and
  checks the real upstream resolver returns the same file.
- Exactly two model paths are required, bound to chat and mmproj in order, inside
  the current HF cache, with matching pinned verification records and current
  sizes. A backend that cannot bind those files fails rather than downloading
  arbitrary default model revisions.
- Model content SHA verification remains in the immediately preceding build
  stage. The handoff size/record check is not a second model SHA verification and
  does not authorize cross-run cache reuse or an old checkpoint reset.
- The executable --version probe must succeed under the same runtime library
  path as backend startup. A loader error cannot be recorded as a successful
  version line.
- Both UI/backend ports are checked. The adapter replaces upstream's broad pkill
  cleanup with a port-availability check; occupied ports fail without killing
  another process or silently changing ports.
- Venv/PATH remain explicit; stale PYTHONPATH is removed, and real CLI build/trial
  require their run-local upstream source. An inherited GPU profile cannot override
  actual detection unless the launch caller explicitly supplies a profile.
- Effective context is measured after load. If OOM recovery reduced the context
  from8192, the trial fails rather than reporting a compliant8192 measurement.
- GPU information is sampled before model load as well as after response. Error
  reports add only an allowlisted reason code, never raw commands, credentials,
  config/environment dumps or an unfiltered exception.

## CPU contract verification

Regression uses real independent build/trial processes, real pinned upstream
configuration, app composition, model manager, backend resolver/startup, localhost
health checks and authenticated streaming. A local fixture executable and tiny
synthetic model files replace GPU/model computation; only Gradio UI is stubbed.
Clean, stale-binary-environment, stale-PATH and stale-profile cases exercise exact
binary/model/mmproj/ctx/port/auth/cache/db/venv/library-path binding and cleanup.
A separate no-torch check confirms nvidia-smi fallback selects the A10080 profile.
These are CPU contract tests, never GPU model or performance acceptance evidence.

## Immutable run and diagnostic scope

Existing failed trials remain terminal. This revision does not reset checkpoints,
reuse selftest receipts, shorten formal packet windows or rewrite old reports.
New formal trials still need fresh run identities and the established gates.
A separately approved short cached-artifact diagnostic would need its own new
identity, immutable old-artifact reads, fresh model hashes and cost/Stop limits,
and a distinct result explicitly excluded from formal performance acceptance.
No such diagnostic is authorized merely by this source artifact.
