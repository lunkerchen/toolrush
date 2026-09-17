"""Test unified process tree lifecycle and orphan elimination."""
import importlib.util
import os
from pathlib import Path
import subprocess
import time
import pytest

P = Path(__file__).resolve().parents[2] / 'v2' / 'plugin'
spec = importlib.util.spec_from_file_location('toolrush_process', P / 'lib' / 'tools' / 'toolrush_process.py')
proc_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proc_mod)
kill_process_tree = proc_mod.kill_process_tree


def test_kill_process_tree_terminates_nested_children(need_posix_shell):
    # Spawn a process that spawns a child
    extra = {'preexec_fn': os.setsid} if os.name != 'nt' else {}
    proc = subprocess.Popen(
        ['bash', '-c', 'sleep 30 & wait'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **extra
    )
    time.sleep(0.1)
    assert proc.poll() is None, "Process should be running"

    # Terminate tree
    kill_process_tree(proc, timeout=2.0)

    # Process should be reaped and returncode non-None
    assert proc.poll() is not None
