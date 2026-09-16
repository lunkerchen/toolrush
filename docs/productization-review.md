# ToolRush Productization Review & Acceptance Report

This document audits the ToolRush productization specification against reproducible local evidence, actual production code paths, test execution outputs, and explicit requirement boundaries. It replaces prior all-green assertions with a factual accounting of what has been implemented and what remains unverified or incomplete.

---

## 1. Executive Summary

ToolRush has been significantly hardened from an experimental benchmark codebase toward an auditable, secure, and maintainable Hermes plugin:
- **Installer Signal & Rollback Safety**: `scripts/install.sh` has been upgraded with signal trap handling (`SIGINT` -> 130, `SIGTERM` -> 143), atomic same-filesystem staging and backup retention, reliable rollback of previous installations on failure or interruption before releasing the lock file (`.toolrush_install.lock`), and checksum-verified versioned release archive installation with safe zip extraction (rejecting directory traversal and symlinks).
- **Honest Discovery in Doctor**: `v2/plugin/doctor.py` accurately inspects executables without fallback assumptions, discovers Hermes packages without blind inference, gates smoke tests on actual execution of warm-shell subshells in isolated environments, and exits with a nonzero code (2) if degraded or when smoke tests fail.
- **Deterministic Packaging Script**: `scripts/package_release.py` extracts release artifact creation into a tested, reusable tool with fixed zip timestamps (`2026-01-01 00:00:00`), normalized file attributes/permissions, sorted walks, and automated generation of `SHA256SUMS` and `MANIFEST.json`.
- **Production Code Test Contracts**: Replaced synthetic simulations and fake passes (`assert True` on non-Windows) with 57 automated pytest tests exercising actual production modules (`tools.toolrush_process`, `tools.toolrush_shell`, `tools.toolrush_rpc`, `agent.toolrush_admission`, and `package_release`).
- **Granular Deficiencies Acknowledged**: Critical areas such as live background RPC thread budgets, cancellation across active workers, Windows bytecode patch execution on real Windows runners, and secret filtering proofs remain documented as partial or unverified rather than falsely claimed complete.

---

## 2. Requirement-by-Requirement Acceptance Matrix

| Requirement ID | Spec Target | Status | Observed Evidence & Verification |
| :--- | :--- | :--- | :--- |
| **P0-1 Installer Safety** | Atomic replace, signal trap rollback, release archive path, no unpinned main fallback | **VERIFIED (POSIX)** | `scripts/install.sh`. 8 integration tests pass in `tests/integration/test_installer_safety.py` (atomic upgrade, archive extraction, traversal rejection, signal trap rollback, lock mutual exclusion). Note: Windows `install.ps1` pending signal test parity. |
| **P0-2 Cross-platform Doctor** | Dynamic discovery, honest status schema, genuine isolated smoke run, nonzero exit on error | **VERIFIED (POSIX)** | `v2/plugin/doctor.py`. Verified on macOS (`python3 v2/plugin/doctor.py --smoke` exit 0; degraded environments exit 2). Tested in `tests/compatibility/test_compatibility_matrix.py`. Windows native execution remains unverified locally. |
| **P0-3 Admission Hardening** | Hostile config, git ext-diff, textconv, curlrc, rg pre/config paths, env assignments | **VERIFIED** | `v2/plugin/lib/agent/toolrush_admission.py`. 8 hostile admission tests pass in `tests/admission/test_hostile_admission.py`. Curl blocked from parallel lanes; Git scheduled sequentially. |
| **P0-4 Version Consistency** | Single version truth across README, plugin.yaml, doctor, version.py | **VERIFIED** | `v2/plugin/version.py` (`2.1.0`), asserted in `tests/unit/test_version_consistency.py` and `tests/unit/test_release_packaging.py`. |
| **P1-1 CI Matrix** | GitHub Actions for macOS, Windows, Ubuntu on Python 3.11 & 3.12 | **PARTIAL / PENDING** | Workflow files configured (`.github/workflows/test.yml` and `release.yml`). Remote multi-platform CI execution has not been executed on GitHub runners from this local repository. |
| **P1-2 Canonical Tests** | Warm-shell, RPC parallel, admission, compatibility, process lifecycle | **VERIFIED (POSIX)** | 57 canonical tests pass via `pytest tests/`. Synthetic mocks and fake passes removed. |
| **P1-3 Subprocess Lifecycle** | Tree termination wired into runtime paths, test orphans and signals | **VERIFIED** | `v2/plugin/lib/tools/toolrush_process.py` wired into `toolrush_shell.py` and `toolrush_rg.py`. Tested in `tests/unit/test_orphan_processes.py` (both POSIX process groups and Windows taskkill/win32 contracts). |
| **P1-4 Repo Hygiene** | Separate production, benchmark, and legacy artifacts | **VERIFIED** | Production code isolated in `v2/plugin/`, benchmarks under `benchmarks/`, legacy v1 code under `legacy/v1/`. |
| **P1-5 Compatibility Matrix** | Documented compatibility across Hermes, Python, Windows, macOS | **PARTIAL** | `docs/compatibility.md` documents runtime expectations, but Windows Python 3.11 bytecode patching remains unverified against live Windows OS. |
| **P2 Release Engineering** | Deterministic zip, SHA256SUMS, GitHub Release, packaging script | **VERIFIED** | Implemented in `scripts/package_release.py` and wired to `.github/workflows/release.yml`. Tested in `tests/unit/test_release_packaging.py`. |

---

## 3. Granular Remaining Gaps & Unverified Items

The following items from the original productization specification are NOT yet fully closed:

1. **RPC Worker In-Flight Cancellation & Budgets**:
   - `execute_read_batch` in `v2/plugin/lib/tools/toolrush_rpc.py` checks `stop_event.is_set()` before dispatch and before each individual task invocation. However, once a worker thread starts executing a blocking tool (such as network `web_search` or long disk read), there is no preemption or socket-level interrupt to cancel running threads midway.
   - Per-request and per-thread memory and output budgets are enforced via global counters and slice previews, but dynamic memory cap policing is not implemented.
2. **Thread Context & Secret Filtering Guarantees**:
   - ToolRush delegates execution to the underlying tool handlers. While snapshot dumps exclude session keys, there is no cryptographic guarantee or live proof that tools called in parallel threads cannot leak process-global secrets if an unisolated third-party tool is invoked.
3. **Windows Bytecode Patching Execution**:
   - The Windows patching mechanism in `v2/plugin/compat.py` relies on exact bytecode sequences and Python 3.11 opcodes. This has only been tested via mock and hash validation on macOS Darwin arm64; no live Windows CI or bare-metal test run has verified actual bytecode monkey-patching in Python 3.11 on Windows.
4. **Hermes Version & Python Version Matrix**:
   - Hermes compatibility has only been validated against installed local `hermes-agent` v0.21.3 on Python 3.12. Older or newer Hermes versions (or Python 3.10/3.11 runtimes) remain untested locally.
5. **Performance Benchmark Regression Verification**:
   - While benchmark scripts exist in `benchmarks/scripts/`, no fresh end-to-end benchmark comparison has been run against a bare Hermes baseline to prove that the p50 latency invariant (< 15% regression) holds after the safety checks were added.

---

## 4. Packaging & Release Engineering

- **Reusable Packaging Tool**: `scripts/package_release.py` replaces inline shell heredocs with an auditable Python script:
  - Preserves deterministic zip attributes: file timestamps are pegged to `2026-01-01 00:00:00 UTC`, directories and filenames are lexicographically sorted, file modes are normalized (0o755 for executables, 0o644 for standard files), and `__pycache__`, `.pyc`, `.git`, and OS metadata files are excluded.
  - Generates `SHA256SUMS` and `MANIFEST.json` containing the artifact name, git tag, version, and SHA256 hex digest.
- **Workflow Integration**: `.github/workflows/release.yml` invokes `scripts/package_release.py --version ""`, verifies `sha256sum -c SHA256SUMS`, and validates that git tags match `v2/plugin/version.py`.

---

## 5. Process & Subprocess Safety

- **Process Tree Reaping**: `kill_process_tree` in `v2/plugin/lib/tools/toolrush_process.py` provides clean cross-platform termination:
  - On POSIX, uses process groups (`os.killpg(pgid, SIGTERM)` followed by `SIGKILL` if still alive after timeout).
  - On Windows, uses `agent.deadline.win_kill` if available, falling back to `taskkill /F /T /PID <pid>`, and finally `proc.kill()`.
- **Warm Shell Broker Lifecycle**: `v2/plugin/lib/tools/toolrush_shell.py` integrates `kill_process_tree` directly into `WarmShell.close()`, ensuring that background jobs spawned inside the subshell are terminated when the broker exits.

---

## 6. Admission & Security Fail-Closed Invariants

`v2/plugin/lib/agent/toolrush_admission.py` enforces strict fail-closed constraints on parallel lanes:
- **Exclusion of Mutating and Unbounded Tools**: Only `read_file`, `search_files`, `web_search`, and `web_extract` can ever be admitted to parallel execution.
- **Curl & Git Conservative Routing**: `curl` is barred from parallel execution due to external endpoint and config side-effects. `git` commands are barred from parallel lanes because arbitrary repository config flags (`diff.external`, `textconv`, `core.fsmonitor`) cannot be safely audited without complete environment sanitization.
- **Ripgrep Hostile Options**: Ripgrep commands containing `--pre`, `--hostname-bin`, `-z`, or hostile `RIPGREP_CONFIG_PATH` fail closed.

---

## 7. Test Suite Audit & Canonical Evidence

All 57 tests run locally via `python3 -m pytest tests/ -v`:

```text
tests/admission/test_hostile_admission.py (8 passed)
  - test_curl_disallowed_from_parallel
  - test_git_commands_rejected_for_conservative_sequential_admission
  - test_git_external_diff_and_config_injection
  - test_rg_hostile_flags
  - test_rg_hostile_config_path
  - test_rg_safe_reads
  - test_shell_syntax_side_effects
  - test_mutators_rejected

tests/compatibility/test_compatibility_matrix.py (5 passed)
  - test_payload_python_version_gate
  - test_all_helper_blobs_match_checksums
  - test_tampered_helper_fails_closed
  - test_unknown_function_patch_fails_closed
  - test_doctor_smoke_in_isolated_process

tests/integration/test_installer_safety.py (8 passed)
  - test_initial_install_succeeds
  - test_repeated_upgrade_no_nesting
  - test_unpinned_fallback_refused_by_default
  - test_concurrent_installation_refused
  - test_verification_failure_rolls_back_and_retains_backup
  - test_release_archive_installation
  - test_archive_path_traversal_rejected
  - test_signal_interruption_restores_backup

tests/integration/test_rpc_parallel_contract.py (21 passed)
  - test_order_durations_and_log
  - test_single_call_skips_the_pool
  - test_rejected (empty, oversized, non-list, None)
  - test_max_batch_accepted
  - test_non_read_tool (write_file, terminal, rm, edit_file)
  - test_read_tool_outside_allowed_tools
  - test_malformed_call (missing args, non-dict args)
  - test_batch_refused_whole (budget overflow)
  - test_batch_exactly_at_budget_runs
  - test_interrupted_before_dispatch
  - test_interrupted_during_dispatch
  - test_raising_and_failing_calls_are_contained
  - test_non_json_dispatch_output_passes_through

tests/integration/test_warm_shell_contract.py (5 passed)
  - test_build_frame_delimiters_and_exports
  - test_build_frame_snapshot_rewrite
  - test_warmshell_execution_and_streaming
  - test_warmshell_exit_code_propagation
  - test_warmshell_process_tree_cleanup

tests/unit/test_orphan_processes.py (4 passed)
  - test_posix_orphan_process_elimination
  - test_windows_uses_win32_kill_when_available
  - test_windows_falls_back_to_taskkill_force_tree
  - test_windows_last_resort_kill_when_taskkill_fails

tests/unit/test_process_lifecycle.py (1 passed)
  - test_kill_process_tree_terminates_nested_children

tests/unit/test_release_packaging.py (4 passed)
  - test_single_version_truth_matches_plugin_yaml
  - test_build_zip_is_deterministic
  - test_package_release_emits_valid_sha256sums_and_manifest
  - test_version_gate_rejects_mismatched_tag

tests/unit/test_version_consistency.py (1 passed)
  - test_version_single_source_of_truth
```

---

## 8. Unverified / Pending Cross-Platform Validations

Before ToolRush can be considered production-ready on non-POSIX platforms, the following validations must be performed on real environments:
1. **Windows 11 / Server 2022 Physical Runners**:
   - Execute `install.ps1` with PowerShell 5.1 and 7.x.
   - Run `python v2/plugin/doctor.py --smoke` on Windows with MSYS2/Git Bash installed.
   - Verify Python 3.11 bytecode patching on Windows x64.
2. **GitHub Actions Remote Pipeline Execution**:
   - Push release tag to trigger `.github/workflows/release.yml` and `.github/workflows/test.yml` on actual GitHub Actions infrastructure.
3. **Live Benchmark Run Under Real Agent Gateways**:
   - Measure real latency and token throughput in an active Hermes gateway session with ToolRush enabled vs disabled.
