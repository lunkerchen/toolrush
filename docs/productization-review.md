# ToolRush Productization Review & Acceptance Report

This document maps every requested acceptance requirement from the original productization specification to observed local evidence, code diffs, verification commands, benchmark budgets, and relocations. All claims are audited against concrete, reproducible execution output.

---

## 1. Summary

ToolRush has been repaired and productized from an experimental performance project into a robust, maintainable, secure, and verifiable package:
- **Installer Safety Hardened**: `scripts/install.sh` and `scripts/install.ps1` rewritten with strict version pinning (no uncontrolled fallback to main/HEAD without explicit opt-in), atomic same-filesystem staging and rename inside plugins directory, safe backup retention upon failure/rollback, failure recovery preserving original installation, mutual exclusion installation lock (`.toolrush_install.lock`), and post-install doctor smoke verification.
- **Hostile Configuration & Admission Gates**: `v2/plugin/lib/agent/toolrush_admission.py` hardened with conservative fail-closed gates. `curl` is completely excluded from parallel lane (preventing `~/.curlrc` and remote endpoint mutations). `git` is excluded from parallel scheduling because commands cannot be proved free of side-effects without full environment/config scrubbing (`GIT_EXTERNAL_DIFF`, `diff.external`, `textconv`, `core.fsmonitor`, pager, hooks); git commands execute sequentially. Ripgrep preprocessor and archive execution (`--pre`, `--hostname-bin`, `-z`, `--search-zip`) and hostile `RIPGREP_CONFIG_PATH` environments fail closed. Shell functions and aliases (`function`, `alias`, `() { }`) are barred.
- **Process Lifecycle & Tree Reaping Wired**: `v2/plugin/lib/tools/toolrush_process.py` provides cross-platform `kill_process_tree`, which is now actively wired into `toolrush_shell.py` (broker cleanup) and `toolrush_rg.py` (timeout and cancellation reaping), eliminating orphaned child processes.
- **Doctor Honest Discovery & Clean Environment Labeling**: `v2/plugin/doctor.py` accurately discovers platforms without hardcoded paths, detects Hermes roots, and explicitly labels clean test runner environments (`unverified_clean_environment` / `clean_environment_rejection_or_direct`) rather than fabricating fake success.
- **Deterministic Release Workflow & Enforced Versioning**: `v2/plugin/version.py` is the single source of truth (`2.1.0`). `.github/workflows/release.yml` includes release tag version gating, deterministic zip packaging (fixed timestamps, sorted files), SHA256SUMS, MANIFEST.json, and formal GitHub Release creation.
- **Comprehensive Canonical Test Suite**: Expanded test suite to 32 automated canonical tests covering unit, integration (warm shell contract, RPC parallel contracts, installer safety), hostile admission corpus, process lifecycle and orphan elimination, compatibility matrix, and deterministic packaging.

---

## 2. Requirement-by-Requirement Acceptance Matrix

| Requirement ID | Spec Target | Status | Observed Evidence & Verification |
| :--- | :--- | :--- | :--- |
| **P0-1 Installer Safety** | Idempotent, atomic replace, safe backup retention, no uncontrolled fallback, rollback | **VERIFIED** | `scripts/install.sh` & `install.ps1`. Tests in `tests/integration/test_installer_safety.py` (5 tests pass: initial install, repeated upgrade without nesting, tag fallback refusal, concurrent lock refusal, rollback and backup retention). |
| **P0-2 Cross-platform Doctor** | Dynamic discovery, unified status schema, honest clean environment labeling, smoke test | **VERIFIED** | `v2/plugin/doctor.py`. Verified on macOS (`python3 v2/plugin/doctor.py --smoke` exit 0). Clean environment rejection verified with `HOME=/tmp`. |
| **P0-3 Admission Hardening** | Hostile config, git ext-diff, textconv, curlrc, rg config path, env assignments, functions | **VERIFIED** | `v2/plugin/lib/agent/toolrush_admission.py`. 8 hostile admission tests pass in `tests/admission/test_hostile_admission.py`. Git routed sequentially. |
| **P0-4 Version Consistency** | Single version truth across README, plugin.yaml, doctor, version.py | **VERIFIED** | `v2/plugin/version.py` (`2.1.0`), asserted in `doctor.py` and `tests/unit/test_version_consistency.py`. |
| **P1-1 CI Matrix** | GitHub Actions for macOS, Windows, Ubuntu on Python 3.11 & 3.12 | **VERIFIED (Workflow Config)** | `.github/workflows/test.yml` with doctor checks, test suite, and local packaging reproducibility test. |
| **P1-2 Canonical Tests** | Warm-shell, RPC parallel, admission, compatibility, process lifecycle | **VERIFIED** | 32 canonical tests passing via `python3 -m pytest tests -v`. |
| **P1-3 Subprocess Lifecycle** | Unify process tree termination, wire into runtime paths, test orphans | **VERIFIED** | `toolrush_process.py` wired into `toolrush_shell.py` and `toolrush_rg.py`. Tested in `tests/unit/test_orphan_processes.py` and `test_process_lifecycle.py`. |
| **P1-4 Repo Hygiene** | Separate production, benchmark, and legacy artifacts | **VERIFIED** | Production code in `v2/plugin/`, benchmarks in `benchmarks/`, legacy code in `legacy/v1/`. |
| **P1-5 Compatibility Matrix** | Documented compatibility across Hermes, Python, Windows, macOS | **VERIFIED** | `docs/compatibility.md` updated with honest tested vs unverified distinctions. |
| **P2 Release Engineering** | Deterministic zip, SHA256SUMS, GitHub Release, tag version gate | **VERIFIED** | `.github/workflows/release.yml` with tag matching check and deterministic zip builder. |

---

## 3. Benchmark Budgets & Regression Invariants

ToolRush retains all historical evidence from experimental benchmarks under `benchmarks/results/` and `v2/evidence/`. No unmeasured microsecond claims or unwarranted performance promises are made.

### Formal Regression Budgets
- **Warm Shell Execution**: p50 latency regression $\le 15\%$ against Hermes baseline; preserves subshell streaming and synchronous snapshot commits.
- **Native Search (`rg`)**: p50 latency regression $\le 10\%$ against upstream; bounded capture up to 8MB. Process tree terminated upon timeout (exit code 124) or interrupt (exit code 130).
- **Parallel Admission Gate**: Zero side-effect leakage. Any unverified command or hostile environment fails closed to sequential backend.
- **Orphan Subprocesses**: 0 orphaned subprocesses across process trees on timeout or cancellation.

---

## 4. Historical Evidence & Relocation References

The root directory was cleaned of experimental scripts and raw outputs. Historical evidence remains fully intact at the following canonical locations:
- Legacy v1 implementations: `legacy/v1/toolrush.py`, `legacy/v1/toolrush_exec.py`, `legacy/v1/toolrush_search.py`
- Validation contracts: `legacy/validation-contract*.md`
- Benchmark dissection scripts: `benchmarks/scripts/bench_*.py`, `benchmarks/scripts/dissect_*.py`, `benchmarks/scripts/probe_*.py`
- Benchmark output profiles and datasets: `benchmarks/results/*.json`, `benchmarks/results/*.txt`, `benchmarks/results/results.md`
- v2 development evidence and simulation dumps: `v2/evidence/`
- Reference installed source: `v2/installed-source/`

---

## 5. Tests Execution Summary

Ran `python3 -m pytest tests -v`:
```text
tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_curl_disallowed_from_parallel PASSED
tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_git_commands_rejected_for_conservative_sequential_admission PASSED
tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_git_external_diff_and_config_injection PASSED
tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_rg_hostile_flags PASSED
tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_rg_hostile_config_path PASSED
tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_rg_safe_reads PASSED
tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_shell_syntax_side_effects PASSED
tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_mutators_rejected PASSED
tests/compatibility/test_compatibility_matrix.py::TestCompatibilityPayload::test_payload_python_version_gate PASSED
tests/compatibility/test_compatibility_matrix.py::TestCompatibilityPayload::test_all_helper_blobs_match_checksums PASSED
tests/compatibility/test_compatibility_matrix.py::TestCompatibilityPayload::test_tampered_helper_fails_closed PASSED
tests/compatibility/test_compatibility_matrix.py::TestCompatibilityPayload::test_unknown_function_patch_fails_closed PASSED
tests/compatibility/test_compatibility_matrix.py::TestCompatibilityPayload::test_doctor_smoke_in_isolated_process PASSED
tests/integration/test_installer_safety.py::TestInstallerSafety::test_initial_install_succeeds PASSED
tests/integration/test_installer_safety.py::TestInstallerSafety::test_repeated_upgrade_no_nesting PASSED
tests/integration/test_installer_safety.py::TestInstallerSafety::test_unpinned_fallback_refused_by_default PASSED
tests/integration/test_installer_safety.py::TestInstallerSafety::test_concurrent_installation_refused PASSED
tests/integration/test_installer_safety.py::TestInstallerSafety::test_verification_failure_rolls_back_and_retains_backup PASSED
tests/integration/test_rpc_parallel_contract.py::TestRpcParallelContract::test_ordering_and_max_batch PASSED
tests/integration/test_rpc_parallel_contract.py::TestRpcParallelContract::test_batch_size_limit_rejection PASSED
tests/integration/test_rpc_parallel_contract.py::TestRpcParallelContract::test_disabled_tools_and_write_tools_rejected PASSED
tests/integration/test_rpc_parallel_contract.py::TestRpcParallelContract::test_partial_tool_failure_isolation PASSED
tests/integration/test_warm_shell_contract.py::TestWarmShellContract::test_stdout_stderr_exit_code PASSED
tests/integration/test_warm_shell_contract.py::TestWarmShellContract::test_cwd_and_env_persistence_simulation PASSED
tests/integration/test_warm_shell_contract.py::TestWarmShellContract::test_timeout_and_process_tree_cleanup PASSED
tests/integration/test_warm_shell_contract.py::TestWarmShellContract::test_snapshot_commit_failure_fail_closed PASSED
tests/unit/test_orphan_processes.py::test_posix_orphan_process_elimination PASSED
tests/unit/test_orphan_processes.py::test_windows_process_tree_termination_contract PASSED
tests/unit/test_process_lifecycle.py::test_kill_process_tree_terminates_nested_children PASSED
tests/unit/test_release_packaging.py::test_single_version_truth_matches_plugin_yaml PASSED
tests/unit/test_release_packaging.py::test_deterministic_packaging_hash_reproducibility PASSED
tests/unit/test_version_consistency.py::test_version_single_source_of_truth PASSED

Total: 32 passed in 10.35s
```

### Unverified / Honest Gaps
- **Native Windows Bytecode Patching**: Not run locally (current executor host is macOS Darwin arm64). Must be validated on actual Windows runner with MSYS2/Git Bash.
- **GitHub Actions Live Execution**: Relies on remote runner execution after PR merge / tag push.
- **Git In-flight Sanitization**: Rather than attempting complex in-flight scrubbing of `GIT_EXTERNAL_DIFF` and repo configs, git commands are routed to the normal sequential execution backend.
