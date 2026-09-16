"""Unified cross-platform process lifecycle and process-tree management.

Provides clean termination of processes and their child trees to guarantee
zero orphaned subprocesses after cancellation, timeout, or broker teardown.
"""
import os
import signal
import subprocess
import time

# Every POSIX child ToolRush spawns must lead its own session, so the process
# group kill below can reach the whole tree without ever naming the caller's
# group. `start_new_session` replaces `preexec_fn=os.setsid`: preexec_fn forks
# without exec and can deadlock a multi-threaded host on the allocator/GIL.
NEW_SESSION_KWARGS = {} if os.name == 'nt' else {'start_new_session': True}

# Bounded extra wait after escalation, purely to reap the zombie.
_REAP_TIMEOUT = 1.0
_POLL_INTERVAL = 0.01


def _wait_until(proc, deadline):
    """Poll until the process exits or the graceful deadline passes."""
    while True:
        if proc.poll() is not None:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_POLL_INTERVAL, remaining))


def _reap(proc):
    """Collect the exit status so this call never leaves a zombie behind."""
    for _ in range(2):
        try:
            proc.wait(timeout=_REAP_TIMEOUT)
            return
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass


def _posix_group_target(pid):
    """Return a process group safe to signal, or None to signal only the pid.

    A child started without ``start_new_session`` shares the caller's process
    group. Calling ``killpg`` on that group would signal this process and the
    gateway hosting it, so the caller's own group is never a valid target.
    """
    try:
        pgid = os.getpgid(pid)
    except (OSError, ProcessLookupError):
        return None
    if pgid in (os.getpgrp(), os.getpgid(0)):
        return None
    return pgid


def _posix_signal(pid, pgid, sig):
    try:
        if pgid is None:
            os.kill(pid, sig)
        else:
            os.killpg(pgid, sig)
    except (OSError, ProcessLookupError):
        pass


def kill_process_tree(proc: subprocess.Popen, timeout: float = 3.0) -> None:
    """Terminate and reap a process and all its children across platforms.

    ``timeout`` is the graceful window: a process that exits on SIGTERM within
    it is never SIGKILLed. Escalation is followed by a short bounded reap.
    """
    if proc.poll() is not None:
        _reap(proc)
        return

    pid = proc.pid
    deadline = time.monotonic() + timeout

    if getattr(os, 'name', '') == 'nt':
        try:
            from agent.deadline import kill_process_tree as win_kill
            win_kill(pid)
        except Exception:
            try:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                               capture_output=True, timeout=timeout)
            except Exception:
                pass
        if not _wait_until(proc, deadline):
            try:
                proc.kill()
            except OSError:
                pass
    else:
        pgid = _posix_group_target(pid)
        _posix_signal(pid, pgid, signal.SIGTERM)
        if not _wait_until(proc, deadline):
            _posix_signal(pid, pgid, signal.SIGKILL)

    _reap(proc)
