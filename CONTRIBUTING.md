# Contributing to ToolRush

## Development Workflow

1. **Test Driven**: Before submitting changes, add regression tests under `tests/`.
2. **Doctor Verification**: Ensure `python3 v2/plugin/doctor.py --smoke` passes without errors or degraded lanes.
3. **Cross-Platform Awareness**: Test or document behaviors separately for Windows and macOS/Linux.
4. **Hook Enforcements**: Always verify local linters and tests before committing.
