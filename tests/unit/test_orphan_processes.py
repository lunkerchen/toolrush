"""Test orphan processes elimination on POSIX and documented Windows behavior."""
import contextlib
import os
import subprocess
import sys
import time
import types
import pytest
from pathlib import Path
import importlib.util

P = Path(__file__).resolve().parents[2] / 'v2' / 'plugin'
spec = importlib.util.spec_from_file_location('toolrush_process', P / 'lib' / 'tools' / 'toolrush_process.py')
proc_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proc_mod)
kill_process_tree = proc_mod.kill_process_tree

def test_posix_orphan_process_elimination():
    if os.name == 'nt':
        pytest.skip("POSIX process group orphan test skipped on Windows")
    
    # Spawn a background sleep that would orphan if parent dies without group kill
    proc = subprocess.Popen(
        ['bash', '-c', 'sleep 50 & sleep 50 & wait'],
        preexec_fn=os.setsid,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(0.1)
    pgid = os.getpgid(proc.pid)
    assert proc.poll() is None

    # Kill process tree
    kill_process_tree(proc, timeout=2.0)
    assert proc.poll() is not None

    # Verify no processes remaining in that pgid
    # os.killpg with signal 0 checks if any process in process group exists
    time.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)

class _FakeProc:
    """Minimal Popen stand-in: alive until something kills it."""

    def __init__(self, pid=4242):
        self.pid = pid
        self.alive = True
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def kill(self):
        self.killed = True
        self.alive = False

    def wait(self, timeout=None):
        self.alive = False
        return 0


@contextlib.contextmanager
def _windows_env(monkeypatch, deadline_module):
    """Make kill_process_tree take its Windows branch, with no POSIX calls left reachable."""
    monkeypatch.setattr(proc_mod.os, 'name', 'nt', raising=False)
    # POSIX group kills must never fire on Windows.
    monkeypatch.setattr(proc_mod.os, 'getpgid',
                        lambda pid: pytest.fail("os.getpgid called on Windows path"),
                        raising=False)
    monkeypatch.setattr(proc_mod.os, 'killpg',
                        lambda pgid, sig: pytest.fail("os.killpg called on Windows path"),
                        raising=False)
    # `None` in sys.modules makes `from agent.deadline import ...` raise ImportError.
    monkeypatch.setitem(sys.modules, 'agent.deadline', deadline_module)
    yield


def test_windows_uses_win32_kill_when_available(monkeypatch):
    proc = _FakeProc(pid=1234)
    calls = []

    def win_kill(pid):
        calls.append(pid)
        proc.alive = False

    deadline = types.ModuleType('agent.deadline')
    deadline.kill_process_tree = win_kill

    ran = []
    monkeypatch.setattr(proc_mod.subprocess, 'run',
                        lambda *a, **kw: ran.append(a) or subprocess.CompletedProcess(a, 0))

    with _windows_env(monkeypatch, deadline):
        kill_process_tree(proc, timeout=2.0)

    assert calls == [1234], "win32 kill_process_tree must be called with the pid"
    assert ran == [], "taskkill fallback must not run when win32 kill succeeds"
    assert proc.poll() is not None


def test_windows_falls_back_to_taskkill_force_tree(monkeypatch):
    proc = _FakeProc(pid=5678)
    ran = []

    def fake_run(cmd, **kwargs):
        ran.append((cmd, kwargs))
        proc.alive = False
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(proc_mod.subprocess, 'run', fake_run)

    # agent.deadline unavailable -> ImportError -> taskkill fallback
    with _windows_env(monkeypatch, None):
        kill_process_tree(proc, timeout=2.0)

    assert len(ran) == 1, "taskkill must be invoked exactly once"
    cmd, kwargs = ran[0]
    assert cmd == ['taskkill', '/F', '/T', '/PID', '5678'], (
        "must force-kill the whole tree (/F /T) by PID"
    )
    assert kwargs.get('timeout') == 2.0
    assert proc.poll() is not None


def test_windows_last_resort_kill_when_taskkill_fails(monkeypatch):
    proc = _FakeProc(pid=9999)

    def fake_run(cmd, **kwargs):
        raise OSError("taskkill missing")

    monkeypatch.setattr(proc_mod.subprocess, 'run', fake_run)

    with _windows_env(monkeypatch, None):
        kill_process_tree(proc, timeout=1.0)

    assert proc.killed, "proc.kill() must run when both Windows kill paths fail"
    assert proc.poll() is not None
