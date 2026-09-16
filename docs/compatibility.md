# ToolRush Compatibility Matrix

This document defines the exact compatibility status of ToolRush across operating systems, Python versions, and Hermes Agent releases.

## Status Definitions

- **Supported & Tested**: Verified by local/CI automated test execution in this repository.
- **Expected-compatible**: Architecture aligns and has no blockers, but relies on cross-platform CI matrix runs.
- **Degraded / Fallback**: Safe sequential or upstream execution with optimization disabled.
- **Unsupported**: Blocked by hard platform constraints or missing dependencies.
- **Not Verified**: Environments not available in the current development context.

---

## Operating System & Feature Matrix

| OS / Platform | Warm Shell (POSIX/Win) | Native Read / Search | Parallel RPC | Admission Hardening | Overall Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **macOS (Darwin ARM64/x64)** | **Ready (ToolRush)** | Handled by Upstream | Sequential / Worker | **Active (ToolRush)** | **Supported & Tested** |
| **Linux (Ubuntu/Debian)** | **Ready (ToolRush)** | Handled by Upstream | Sequential / Worker | **Active (ToolRush)** | **Supported & Tested** |
| **Windows 10/11 (MSYS/Git Bash)**| **Ready (ToolRush)** | **Ready (ToolRush Bytecode)** | **Ready (ToolRush UDS)** | **Active (ToolRush)** | **Supported (CI Tested)** |

---

## Hermes & Python Matrix

| Hermes Version | Python Version | Windows Status | macOS / Linux Status | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Hermes Agent v0.21.x** | **Python 3.11** | **Supported & Tested** | **Supported & Tested** | Full bytecode patching on Windows; native lanes on macOS. |
| **Hermes Agent v0.21.x** | **Python 3.12** | **Degraded / Safe Fallback** | **Supported & Tested** | Windows payload is targeted to 3.11 bytecode; 3.12 falls back closed safely. |
| **Hermes Agent < 0.20** | **Python 3.11+** | Unsupported | Unsupported | Requires Hermes Plugin architecture. |
| **Other / Unknown** | Any | Fails closed (Doctor exits 2) | Fails closed | Guarded by hash checks and AST validation. |

---

## Safety & Invariant Guarantees

1. **Authorization Unchanged**: ToolRush never bypasses Hermes tool-call confirmation, user prompts, or permission gates.
2. **Read-Only Invariant**: Parallel scheduling only admits proven read-only commands without side-effects. Mutating commands (`curl`, `rm`, `git status`, redirections, etc.) always execute sequentially.
3. **Fail-Closed Drift Protection**: Upstream modifications to patched Hermes functions automatically deactivate the patch row with warnings, falling back to upstream logic.
4. **Zero Orphaned Processes**: Subprocess trees (warm shell and ripgrep) are terminated via process groups on POSIX and job object / taskkill on Windows.
