# October 5 CLI source-root correction / CLI source root修正

## English

Base: `0d376d5ba3ad3f7320c3c774abfc41fdb5974543`. This candidate preserves the October 4 report allowlist/schema correction and all other source files. The source is committed locally for a repinned self-contained delivery candidate; it has not been pushed or deployed.

Confirmed defect: `scripts/pod_run.py` added the upstream import path from `layout.paths()` before parsing `--root`. `stage_source` instead clones to `Runner.p["source"]`. Therefore an explicit root different from `QMC_WORKSPACE` (default `/workspace/qwen`) could produce `ModuleNotFoundError: No module named 'qmc'`, or import a different checkout from the default root. `llama.install` imports `qmc.colab` before CUDA discovery/build commands.

Fix: after argument parsing and Runner creation, add `r.p["source"] / "qwen-multimodal-colab" / "src"` to `sys.path` if it exists. The root default, stage ordering, venv re-execution, background execution and download/build code are unchanged. Early stages still start without upstream installed.

CPU verification (Linux; existing Python 3.11.16 and pytest 9.1.1; no installation):
- New regression on unmodified production code: 8 failed, 5 passed. Explicit absolute/relative roots fail for build/trial, with both absent and conflicting default upstream trees.
- Fixed source suite: 149 passed, 12 skipped, plus 14 unittest subtests reported separately by pytest. This is 135 preexisting source tests + 14 new tests. All skips are existing upstream-dependent tests, not passes.
- New tests use isolated subprocesses (`-I`), unrelated current directories, temporary real `qmc` packages, default-root compatibility and no-upstream selftest. A real build dispatch also reaches `llama.install` and imports a stub `qmc.colab`, then deliberately stops at its pin check, before any CUDA discovery or subprocess.
- The unchanged delivery-helper baseline was rerun separately: 47 passed against its original pinned-source fixture. Those results preceded packaging; the new self-contained delivery candidate separately reruns all helpers against its newly pinned source. Combined independent checks: 196 test methods passed, 12 source skips; no double counting of subtests.

Observed packet identity: run `oct5-chat-111424-synthetic-held-pod`, root `/workspace/oct5-chat-111424-synthetic-held-pod`, source commit as above. Code establishes the corresponding clone location at `<root>/source/qwen-multimodal-colab`. The live failure was reported as `ModuleNotFoundError: No module named qmc`; the exact live Pod environment and complete failure log were not independently available in this CPU review. Thus the defect and local reproduction are confirmed; attribution of the complete live incident remains consistent with, rather than proven solely by, this review.

No GPU work, provider/API calls, downloads, paid resources, Windows operations, public pushes or original ZIP modifications. CUDA compilation, actual upstream dependencies, model hashing/loading, inference and real transport remain unverified. This candidate is not a GPU acceptance result or launch authorization. The self-contained delivery revision records a new local source commit/archive/manifest. Any paid validation still requires explicit approval.

## 日本語

正式基点は `0d376d5ba3ad3f7320c3c774abfc41fdb5974543`。allowlist/schema修正を保持し、独立cloneでCLIの最小変更と回帰テストのみ作成。配布用にローカルcommitするが、未push・未展開。

原因は、CLIが `--root` 解析前に既定 `QMC_WORKSPACE` からimport先を設定する一方、source stageはRunnerのrootへcloneする不一致。指定rootだけに `qmc` があればimport失敗し、既定rootに別checkoutがあれば誤読込する。`llama.install` の `qmc.colab` importはCUDA確認・buildコマンドより前にある。

修正はRunner生成後に同じ `r.p["source"]` からupstream srcを設定するだけ。既定root、venv再実行、background、stage順序等は変更しない。

修正前は新規回帰13件中8件失敗・5件成功。修正後source全体149件成功・既存upstream依存12件skip。新規14件は非既定絶対/相対root、異なるcwd、既定root競合、既定root互換、upstreamなしselftest、実build関数のqmc.colab importをCPUのみで確認。別途、変更のない配布helperの元fixtureで47件成功。合計196件成功、12件skip（subtestは加算しない）。

packetのrun ID/root/commit一致は確認。実Podログ全文・実際の環境変数値はこのレビューでは未確認なので、コード不具合とCPU再現は確定、実Pod障害との完全な因果確定とは分ける。GPU、API、課金、Windows操作、新規install、公開pushは行っていない。旧ZIPを変更せず、自己完結配布版には新しいローカルcommit/archive/manifestを記録。GPU合格や再起動許可を意味しない。

## Live observation supplied after CPU verification / 追加の実Pod観測

The operator directly read the following build.log lines in Jupyter at 11:44:33 JST, before stopping the Pod. No log download or Pod restart was performed in this review:

- `QMC2|oct5-chat-111424-synthetic-held-pod|02:37:18Z|build|state=running`
- `QMC2|oct5-chat-111424-synthetic-held-pod|02:43:15Z|build|state=fail error=ModuleNotFoundError message=No module named 'qmc'`

The reported checkpoint lists selftest/net/source/venv completed, build failed, exit code 1, external operator Stop. These direct operator observations support the incident diagnosis; the live default environment and full log bytes remain unverified here. Pod再起動による追加確認は行っていない。

## Restart and cache limits / 再開・cacheの制約

This change does not authorize or implement same-root resume, checkpoint resets, cache relocation, skipped stages or faster retries. Existing immutable run-ID/root and deadline rules still apply. Reusing a previous Pod volume may retain bytes, but their availability, integrity and compatibility must be verified separately; no saved setup/build/download time is promised.

今回の変更は同じrootでの再開、checkpointリセット、cache移動、stage省略、高速retryを実装・承認しない。既存run ID/rootの不変性と期限規則を維持する。以前のPod volumeにデータが残っていても、存在・整合性・互換性の別途確認が必要で、setup/build/download時間の短縮は未検証。
