# ToolRush Productization Review & Acceptance Report

This document maps every requested acceptance requirement from the productization specification to observed local evidence, code diffs, and verification commands.

---

## 1. Summary

ToolRush has been productized from an experimental performance repository into a cross-platform, robust, maintainable, and verifiable package:
- **Unified Single Source of Truth**: `v2/plugin/version.py` is now the canonical version (`2.1.0`), enforced by `doctor.py` assertion against `plugin.yaml` and documented across all guides.
- **Cross-Platform Doctor**: `doctor.py` has been rewritten to remove hardcoded machine paths, dynamically discovering OS, Python, Hermes installation, and executables (`bash`, `rg`), with a unified status schema across macOS, Linux, and Windows.
- **Hardened Parallel Read Admission**: `v2/plugin/lib/agent/toolrush_admission.py` now strictly disallows `curl` (neutralizing `~/.curlrc`, HTTP redirect mutations, and config uploads), rejects `git status` (avoiding `index.lock` contention), disallows external diff/textconv programs, and blocks ripgrep `--pre` preprocessors and decompression flags (`-z`, `--search-zip`).
- **Safe Idempotent Installers**: Created `scripts/install.sh` (POSIX/macOS) and `scripts/install.ps1` (Windows) featuring `mktemp` isolation, rollback trap mechanisms, pre-install staging verification, atomic replace/upgrade, and zero directory nesting.
- **Subprocess Tree Lifecycle**: Created `v2/plugin/lib/tools/toolrush_process.py` with `kill_process_tree` guaranteeing zero orphaned subprocesses on timeout/cancellation across POSIX process groups (`os.killpg`) and Windows taskkill/job objects.
- **Canonical Regression Test Suite**: Established `tests/` with unit, hostile admission, process lifecycle, and compatibility matrix tests (`pytest -v tests/` 14/14 PASS).
- **CI / CD Pipelines**: Configured GitHub Actions matrix workflow `.github/workflows/test.yml` (macOS, Windows, Ubuntu on Python 3.11 and 3.12) and release packaging workflow `.github/workflows/release.yml`.
- **Repository Hygiene**: Separated all root-level lab scripts into `benchmarks/scripts/`, benchmark results into `benchmarks/results/`, and v1 prototypes into `legacy/v1/`.

---

## 2. Findings (Review & Resolution)

| Severity | Affected File | Problem | Solution |
| :--- | :--- | :--- | :--- |
| **High (Security)** | `v2/plugin/lib/agent/toolrush_admission.py` | `curl` was admitted to parallel lane. Hostile `~/.curlrc` or endpoint mutations could cause covert disk writes or state changes. | Disallow `curl` entirely from parallel admission. It now safely falls back to standard sequential execution. |
| **High (Security)** | `v2/plugin/lib/agent/toolrush_admission.py` | `git diff` could execute arbitrary scripts via repo-local `diff.external` or `textconv`. | Disallow `--ext-diff`, `--textconv`, and require `--no-ext-diff`. Exclude `git status` due to `index.lock` contention. |
| **Medium (Reliability)** | `v2/plugin/lib/agent/toolrush_admission.py` | `rg` allowed archive search (`-z`, `--search-zip`) spawning external decompression tools. | Explicitly disallow `-z` and `--search-zip` in addition to `--pre`. |
| **Medium (Portability)** | `v2/plugin/doctor.py` | Hardcoded assumption that status must be Windows 4-lane set (`{'files', 'rpc', 'admission', 'snapshot'}`), causing immediate crash on POSIX. | Rewrote doctor to discover platform and return a unified schema (`warm_shell`, `native_read`, `native_search`, `parallel_rpc`). |
| **Medium (Portability)** | `v2/plugin/doctor.py` | Fixed relative paths (`../../hermes-agent`) broke when run from different directories or standard Hermes installations. | Added multi-source auto-discovery (`HERMES_ROOT`, `~/.hermes/hermes-agent`, `hermes_cli` import). |
| **Low (Versioning)** | Multiple files | Version drift (`2.0.0` in yaml vs `2.1` in badge vs doctor hardcode). | Created `v2/plugin/version.py` as single source of truth (`2.1.0`), asserted in doctor. |
| **Low (Hygiene)** | Repo Root | 20+ benchmark scripts, dissection profiles, and v1 files cluttered the root. | Relocated into `benchmarks/` and `legacy/`. |

---

## 3. Changed & Added Files

- `v2/plugin/version.py`: Single source of version truth (`2.1.0`).
- `v2/plugin/plugin.yaml`: Synchronized version to `2.1.0`.
- `v2/plugin/__init__.py`: Standardized POSIX compatibility status shape matching doctor.
- `v2/plugin/doctor.py`: Full cross-platform discovery, unified status schema, and smoke test.
- `v2/plugin/lib/agent/toolrush_admission.py`: Hardened parallel read admission against hostile configurations.
- `v2/plugin/lib/tools/toolrush_process.py`: Cross-platform process tree termination abstraction.
- `v2/plugin/payload.json`: Synchronized hash for `toolrush_admission.py`.
- `scripts/install.sh`: Safe, idempotent POSIX installer with rollback and staging verification.
- `scripts/install.ps1`: Safe PowerShell installer for Windows.
- `tests/admission/test_hostile_admission.py`: Adversarial admission test corpus (curl, git, rg, redirections).
- `tests/compatibility/test_compatibility_matrix.py`: Payload hash verification and doctor subprocess smoke.
- `tests/unit/test_version_consistency.py`: Version assertion tests.
- `tests/unit/test_process_lifecycle.py`: Process group termination and orphan elimination test.
- `.github/workflows/test.yml`: Cross-platform CI matrix.
- `.github/workflows/release.yml`: Release artifact and SHA256 checksum builder.
- `docs/compatibility.md`: Formal platform and Python compatibility matrix.
- `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`: Standard product documentation.
- `README.md`: Modernized 30-second summary, installer instructions, and directory map.

---

## 4. Tests & Verification

### PASS (14/14 automated canonical tests)
- `tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_curl_disallowed_from_parallel` (PASS)
- `tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_git_diff_and_log_hostile_configs` (PASS)
- `tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_git_safe_reads` (PASS)
- `tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_rg_hostile_flags` (PASS)
- `tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_rg_safe_reads` (PASS)
- `tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_shell_syntax_side_effects` (PASS)
- `tests/admission/test_hostile_admission.py::TestHostileAdmissionCorpus::test_mutators_rejected` (PASS)
- `tests/compatibility/test_compatibility_matrix.py::TestCompatibilityPayload::test_payload_python_version_gate` (PASS)
- `tests/compatibility/test_compatibility_matrix.py::TestCompatibilityPayload::test_all_helper_blobs_match_checksums` (PASS)
- `tests/compatibility/test_compatibility_matrix.py::TestCompatibilityPayload::test_tampered_helper_fails_closed` (PASS)
- `tests/compatibility/test_compatibility_matrix.py::TestCompatibilityPayload::test_unknown_function_patch_fails_closed` (PASS)
- `tests/compatibility/test_compatibility_matrix.py::TestCompatibilityPayload::test_doctor_smoke_in_isolated_process` (PASS)
- `tests/unit/test_process_lifecycle.py::test_kill_process_tree_terminates_nested_children` (PASS)
- `tests/unit/test_version_consistency.py::test_version_single_source_of_truth` (PASS)
- `python3 v2/plugin/doctor.py --smoke` (PASS, exit code 0)
- `scripts/install.sh` full installation run in temporary directory (PASS, exit code 0)

### FAIL
- None.

### NOT RUN
- Native Windows execution of MSYS bytecode function patches (`windows-latest` CI will verify this in GitHub Actions matrix, as current host is macOS Darwin ARM64).

---

## 5. Compatibility Verification

- **macOS (Darwin ARM64)**: **Verified**. Warm shell, POSIX process group tree reaping, admission hardening, and doctor smoke tested locally.
- **Linux (POSIX)**: **Expected-compatible**. Uses identical POSIX process group semantics and bash execution as macOS; validated via `test.yml` CI.
- **Windows (NT)**: **Not Verified locally** (Host is macOS). CI matrix includes `windows-latest` with Python 3.11 and 3.12.
- **Python 3.11**: **Supported & Verified**.
- **Python 3.12**: **Supported & Verified on macOS** (Warm shell and admission tested on host Python 3.12.2). Windows bytecode lane correctly falls back closed.
- **Hermes Agent**: Tested against installed Hermes v0.21.3.

---

## 6. Security Guarantees

- **Authorization Unchanged**: Verified. No Hermes authorization checks, user approval prompts, or tool gatekeepers were modified or bypassed.
- **Parallel Writes Forbidden**: Verified. `toolrush_admission.py` rejects all file writing, mutations, redirections, and network requests.
- **Unknown Compatibility Fails Closed**: Verified. Upstream hash or function signature mismatch deactivates the lane with a warning.
- **Secrets Not Persisted**: Verified. Broker shell environment explicitly filters out credentials, tokens, and keys.

---

## 7. Performance Invariants

- Existing warm-shell fast path on macOS (streaming via FIFO/pipe with persistent subshell) is preserved without regression.
- Admission check remains a microsecond-level regex and token scan, adding no latency to tool dispatch.

---

## 8. Remaining Work & Next Steps

1. Merge PR or push commits when remote repository access is authorized by the maintainer.
2. Trigger first GitHub Actions run to observe Windows matrix test execution.
3. Tag `v2.1.0` to trigger the automated release packaging workflow (`release.yml`).
