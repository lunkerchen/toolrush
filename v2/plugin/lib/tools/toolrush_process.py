"""Unified cross-platform process lifecycle and process-tree management.

Provides clean termination of processes and their child trees to guarantee
zero orphaned subprocesses after cancellation, timeout, or broker teardown.
"""
import os
import signal
import subprocess
import time


def kill_process_tree(proc: subprocess.Popen, timeout: float = 3.0) -> None:
    """Terminate and reap a process and all its children across platforms."""
    if proc.poll() is not None:
        return

    pid = proc.pid
    is_windows = getattr(os, 'name', '') == 'nt'

    if is_windows:
        try:
            from agent.deadline import kill_process_tree as win_kill
            win_kill(pid)
        except Exception:
            try:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                               capture_output=True, timeout=timeout)
            except Exception:
                pass
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
    else:
        # POSIX process group termination
        try:
            pgid = os.getpgid(pid)
            # Try gentle SIGTERM first
            os.killpg(pgid, signal.SIGTERM)
            time.sleep(0.05)
            # Escalation to SIGKILL if still alive
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        except (OSError, ProcessLookupError):
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
