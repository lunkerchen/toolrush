# Changelog

All notable changes to ToolRush will be documented in this file.

## [2.1.2] - 2026-09-24

### Fixed
- `doctor.py` on macOS/Linux no longer reports native read/search/parallel RPC as ToolRush lanes that are "ready" without checking. `lanes` now lists only `warm_shell`; upstream capabilities are probed under `upstream` (`parallel_rpc` is `unavailable`: ToolRush ships `parallel()` on Windows only).
- README install commands pointed at a nonexistent `v0.1.0` tag; README and both installers now default to `v2.1.2`.

### Changed
- Version bumped to 2.1.2 so it supersedes an unreleased local 2.1.1 build.

### Added
- `doctor.py --hermes-root`, a `hermes_python` report field, and a warning when doctor runs on a different Python than Hermes.

## [2.1.0] - 2026-09-16

### Added
- Cross-platform safe idempotent installer (`scripts/install.sh` and `scripts/install.ps1`) with atomic staging, verification, and rollback.
- Cross-platform `doctor.py` reporting standardized JSON schema across macOS, Linux, and Windows.
- Hardened parallel read admission: strictly excludes `curl`, `git status`, external git diff/textconv filters, and ripgrep preprocessor/archive flags from parallel lane.
- Canonical test suite under `tests/` covering admission security, process lifecycle, versioning, and compatibility.
- GitHub Actions CI workflow (`.github/workflows/test.yml`) running matrix tests across macOS, Windows, and Linux.
- Release pipeline workflow (`.github/workflows/release.yml`) for reproducible zip packages and SHA256 checksums.
- Clear separation of root directory into `benchmarks/`, `legacy/`, `docs/`, and production `v2/plugin/`.

### Changed
- Unified single source of truth for versioning (`v2/plugin/version.py`).
- Doctor manifest asserts matching version across `plugin.yaml` and `version.py`.
