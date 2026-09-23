# AGENTS.md

Scope for agent changes in this repo.

- In scope: `v2/plugin/` (plugin, doctor), `scripts/`, `tests/`, `README.md`, `CHANGELOG.md`, `.github/workflows/`.
- Out of scope unless asked: `v2/evidence/`, `v2/installed-source/`, `legacy/`, `benchmarks/results/` (historical records, keep byte-identical).
- Never edit the Hermes tree (`~/.hermes/hermes-agent`) or the live install (`~/.hermes/plugins/toolrush`).
- Acceptance: `python3 -m pytest -q tests` green; `python3 v2/plugin/doctor.py --smoke` output passes `scripts/verify_doctor_contract.py`; doctor reports only lanes it verified.
- `gh` default repo is the upstream fork parent; pass `-R lunkerchen/toolrush` explicitly.
- Commits: conventional commits, one concern each. Tags/releases only with owner approval.
