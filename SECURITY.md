# Security Policy

## Security Principles

1. **Fail-Closed Drift Protection**: Any mismatch between upstream Hermes functions and patched AST/bytecode immediately disables the patch row and falls back to upstream.
2. **Zero Parallel Writes**: The parallel RPC lane only admits commands proven to be 100% free of local, environment, or network mutations.
3. **No Secret Persistence**: Persistent warm-shell environments strictly strip session tokens, API keys, and sensitive environment variables from broker processes.
4. **Subprocess Isolation**: Process groups on POSIX and Job Objects on Windows guarantee that cancelled or timed-out tasks never leave orphaned background processes.

## Reporting a Vulnerability

If you discover a security vulnerability in ToolRush, please do not file a public issue. Contact the maintainer directly via GitHub or email.
