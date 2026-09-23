"""ToolRush for macOS — native low-overhead execution layer for Hermes Agent.

Provides:
1. Streaming Warm-Shell transport on macOS using POSIX process groups.
2. Parallel read RPC (`from hermes_tools import parallel`) in `execute_code`.
3. Deterministic LRU caching for command security checks (`tirith`).
"""

import functools
import logging
import os
import re
import shlex
import threading

logger = logging.getLogger("toolrush")

_SPAWN_LOCK = threading.Lock()
_WARM_ATTR = "_toolrush_warm_mac_v2"


def _apply_terminal_lane():
    try:
        from tools.environments import local
        from .lib.warm_shell import MacOSWarmShell, WarmHandle, build_frame

        cls = local.LocalEnvironment
        original_run = cls._run_bash
        original_cleanup = cls.cleanup
        original_kill = cls._kill_process

        if getattr(original_run, "_toolrush_mac", False):
            return

        def run(owner, command, *, login=False, timeout=120, stdin_data=None):
            # Fall back to disposable subshell for login, custom stdin, or background fork
            if login or stdin_data is not None:
                return original_run(owner, command, login=login, timeout=timeout, stdin_data=stdin_data)

            # Check if command runs a background job that might hold stdout
            for line in command.splitlines():
                if line.startswith("eval "):
                    try:
                        user_code = shlex.split(line)[1]
                    except (ValueError, IndexError):
                        user_code = "&"
                    if re.search(r"(?<!&)&(?!&)", user_code):
                        return original_run(owner, command, login=login, timeout=timeout, stdin_data=stdin_data)

            shell = None
            acquired = False
            try:
                with _SPAWN_LOCK:
                    shell = getattr(owner, _WARM_ATTR, None)
                    if shell is None or shell.dead or shell.proc.poll() is not None:
                        if shell is not None:
                            shell.close()
                        shell = MacOSWarmShell(local, owner)
                        setattr(owner, _WARM_ATTR, shell)

                acquired = shell.lock.acquire(blocking=False)
                if not acquired:
                    # Shell busy with concurrent command, fallback cleanly
                    return original_run(owner, command, login=login, timeout=timeout, stdin_data=stdin_data)

                frame, begin, end, commit = build_frame(owner, local, command, timeout)
                return WarmHandle(shell, frame, begin, end, commit)
            except Exception as e:
                if acquired and shell:
                    shell.lock.release()
                logger.debug("ToolRush warm shell bypassed: %s", e)
                return original_run(owner, command, login=login, timeout=timeout, stdin_data=stdin_data)

        def cleanup(owner):
            shell = getattr(owner, _WARM_ATTR, None)
            if shell is not None:
                shell.close()
                setattr(owner, _WARM_ATTR, None)
            return original_cleanup(owner)

        def kill(owner, proc):
            if isinstance(proc, WarmHandle):
                proc.kill()
                return
            return original_kill(owner, proc)

        run._toolrush_mac = True
        run._toolrush_original = original_run
        run.__name__ = "_run_bash"
        cls._run_bash = run
        cls._kill_process = kill
        cls.cleanup = cleanup
        logger.info("ToolRush macOS: Warm-shell lane active")
    except Exception as exc:
        logger.warning("ToolRush macOS: Warm-shell lane failed to attach: %s", exc)


def _apply_rpc_lane():
    try:
        import tools.code_execution_rpc as rpc_mod
        import tools.code_execution_tool as exec_tool
        import tools.code_kernel as code_kernel
        from .lib.parallel_rpc import execute_read_batch

        # 1. Patch CellAuthority.dispatch to support multi-threaded Context execution
        if hasattr(code_kernel, "CellAuthority"):
            orig_ca_dispatch = code_kernel.CellAuthority.dispatch
            if not getattr(orig_ca_dispatch, "_toolrush_mac", False):
                def thread_safe_cell_dispatch(self, tool_name: str, tool_args: dict) -> str:
                    from tools.registry import tool_error
                    if not self.active:
                        return tool_error("No active execute_code cell: the cell this kernel call "
                                          "belonged to has settled, so its tool authority is retired.")
                    return self.ctx.copy().run(self._invoke, tool_name, tool_args)

                thread_safe_cell_dispatch._toolrush_mac = True
                thread_safe_cell_dispatch._toolrush_original = orig_ca_dispatch
                code_kernel.CellAuthority.dispatch = thread_safe_cell_dispatch
                logger.info("ToolRush macOS: CellAuthority thread-safe context patch active")

        # 2. Patch RPC handler
        original_handle = rpc_mod._handle_rpc_request

        if getattr(original_handle, "_toolrush_mac", False):
            return

        def patched_handle_rpc_request(
            request: dict,
            *,
            allowed_tools: frozenset,
            tool_call_counter: list,
            max_tool_calls: int,
            dispatch,
            tool_call_log: list,
            call_start: float,
            where: str,
        ) -> str:
            if request.get("tool") == "parallel":
                return execute_read_batch(
                    request.get("args", {}),
                    allowed_tools=allowed_tools,
                    counter=tool_call_counter,
                    budget=max_tool_calls,
                    log=tool_call_log,
                    dispatch=dispatch,
                )
            return original_handle(
                request,
                allowed_tools=allowed_tools,
                tool_call_counter=tool_call_counter,
                max_tool_calls=max_tool_calls,
                dispatch=dispatch,
                tool_call_log=tool_call_log,
                call_start=call_start,
                where=where,
            )

        patched_handle_rpc_request._toolrush_mac = True
        rpc_mod._handle_rpc_request = patched_handle_rpc_request

        original_gen = exec_tool.generate_hermes_tools_module

        def patched_gen(enabled_tools, transport="uds"):
            code = original_gen(enabled_tools, transport=transport)
            if "def parallel(" not in code:
                code += """

def parallel(calls):
    \"\"\"Execute a batch of 1..16 read operations concurrently and return results in input order.
    Example:
        parallel([{'tool': 'read_file', 'args': {'path': 'a.py'}}, {'tool': 'read_file', 'args': {'path': 'b.py'}}])
    \"\"\"
    return _call('parallel', {'calls': calls})
"""
            return code

        patched_gen._toolrush_mac = True
        exec_tool.generate_hermes_tools_module = patched_gen
        logger.info("ToolRush macOS: Parallel RPC lane active")
    except Exception as exc:
        logger.warning("ToolRush macOS: Parallel RPC lane failed to attach: %s", exc)


def _apply_security_cache():
    try:
        import tools.tirith_security as tirith_mod

        if hasattr(tirith_mod, "check_command_security"):
            orig = tirith_mod.check_command_security
            if not getattr(orig, "_toolrush_mac", False):
                cached = functools.lru_cache(maxsize=1024)(orig)
                cached._toolrush_mac = True
                tirith_mod.check_command_security = cached
                logger.info("ToolRush macOS: Security scan LRU cache active")
    except Exception as exc:
        logger.warning("ToolRush macOS: Security scan cache failed to attach: %s", exc)


def register(ctx=None):
    """Hermes plugin entry point."""
    _apply_terminal_lane()
    _apply_rpc_lane()
    _apply_security_cache()
    return {"status": "ready", "platform": "macOS", "version": "2.1.1"}
