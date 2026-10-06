# Approved CPU preparation: chat-only profile

Approval covers preparation and GPU-free Linux validation only. No resource
create/start, paid model inference, fixture upload or new credential is authorized.

The chat_only packet keeps fresh balance/cost inputs, reserve, actual startedAt,
same-day approval, explicit max runtime and absolute Stop deadline, buffer,
clean source commit, lock and model verification. Its feature_options contain
only allow_public_search=false. Full-feature legacy packets keep their strict
speech hashes and owner attestation checks; they are not approved for this run.

The trial command requests warm measurements and both work/export deadlines.
It does not request features, search, audio or the optional UI hold. The existing
measurement implementation loads the model, records one cold request capped at
64 tokens, then ten warm requests capped at 256 tokens. Diagnostic export and
cleanup are still bounded; the external human Stop operator remains necessary.
Application cancellation/exit never confirms stopped GPU billing.

Reports label chat_only, and ctx_32k, image_understanding, web_search, asr and
history_restore are skipped with not_requested scope. No full-feature acceptance
is claimed. A strict receiver must explicitly select evaluation-profile=chat_only;
its default full-feature acceptance continues to reject these partial reports.
CPU mock mode remains inadmissible as real GPU evidence.

The existing build verifies the pinned chat GGUF and mmproj model files. This
candidate preserves that source/hash path; it does not exercise image inference
or download models during CPU validation. No ASR dataset bytes are distributed.

The candidate is unrun in the Windows Codex execution identity; Python execution
is denied and must not be retried or bypassed. The authorized Linux CPU worker
must execute the portable verifier and pytest modules, report exact identities
and skip reasons, and correct any defects in a separate reviewed revision.
