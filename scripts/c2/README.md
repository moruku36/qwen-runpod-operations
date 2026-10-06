# C2 v5 recovery primitives and inert operator templates

The accepted local C2 v5 installation remains disabled. These public files preserve
its implementation without publishing a machine's paths, owner identity, ACL
snapshot, application user identity, private inputs or runtime records.

`migration-core.ps1`, `rollback-bound-cas.ps1` and `hook-retain.ps1` define helpers
only. Dot-sourcing them does not start a service or call a provider. The public
migration helper accepts an exact physical old root; it omits a machine-specific
virtualized-path alias from the local generated deployment.

`templates/*.in` are **inert review source**, not an install bundle. Their
`@@REVIEWED_*@@` placeholders intentionally do not name a runnable deployment.
Never rename and execute them as an installation shortcut. A future deployment
must supply exact physical paths, the service-owner SID, protected ACL policy,
source/patch pins and an allowlisted payload manifest, then undergo independent
review. The local accepted manifest hashes do not validate these sanitized files.
The Python example refuses its placeholder deployment root before importing the
gateway. Its `main` is tested by AST extraction with a fake gateway, without a bind.

Main rollback verifies and renames the **same exclusive READ+DELETE handle**.
The current bytes must match the reviewed patched or original SHA; an unknown
source, changed backup, held writer, reparse/hardlink or occupied destination
refuses the operation. The verified patched file is retained by no-replace rename,
then the prepared original is installed only into an absent canonical path.
Hook rollback similarly retains the verified hook by no-replace rename, never
unlinking it. Stop markers, private-input JSON and public controller records use
exclusive create; collisions remain for attended review. No restart cleans up a
retained record or flag implicitly.

The two main renames are not an atomic exchange. A crash after displacement can
leave the canonical file absent. Both complete sources remain for manual review.
Recovery must lock and hash the retained original, then rename only into an absent
canonical path; an unknown canonical entry must remain untouched. Directory
namespace changes and modification by the same authorized owner are outside the
guarantee. File hashes alone do not prove process ownership or the root ACL policy.
Controller startup still needs its launch lock and exact owner/process/listener
checks. The start template uses fixed log redirection, which can truncate prior
routine output; preserve previous logs explicitly before an approved restart.

Run only the public synthetic regressions for development:

```powershell
powershell -NoProfile -File tests/test_c2_main_cas.ps1
powershell -NoProfile -File tests/test_c2_hook_races.ps1
python -B -m unittest discover -s tests -p test_c2_controller_records.py
```

The native cases create tiny synthetic files in a fresh temporary directory,
including only fixture ACL changes. They do not access an installed OWUI, read
credentials, run an operator, start/stop a service or call RunPod.
