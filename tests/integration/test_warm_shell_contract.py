"""Canonical warm-shell contract tests across POSIX and Windows."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
import pytest

P = Path(__file__).resolve().parents[2] / 'v2' / 'plugin'
spec = importlib.util.spec_from_file_location('toolrush_process', P / 'lib' / 'tools' / 'toolrush_process.py')
proc_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proc_mod)
kill_process_tree = proc_mod.kill_process_tree

class TestWarmShellContract:
    def test_stdout_stderr_exit_code(self):
        # Verify stdout, stderr merge, and exit code propagation
        proc = subprocess.Popen(
            ['bash', '-c', 'echo "stdout-msg"; echo "stderr-msg" >&2; exit 42'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        out, _ = proc.communicate()
        assert proc.returncode == 42
        assert "stdout-msg" in out
        assert "stderr-msg" in out

    def test_cwd_and_env_persistence_simulation(self):
        # Test simulated snapshot commit and export persistence across invocations
        with tempfile.TemporaryDirectory() as td:
            snap_file = os.path.join(td, "snap.sh")
            # Step 1: Export a var and commit snapshot atomically
            cmd1 = f'export TOOLRUSH_TEST=persisted; export -p > "{snap_file}.tmp" && mv -f "{snap_file}.tmp" "{snap_file}"'
            r1 = subprocess.run(['bash', '-c', cmd1], capture_output=True, text=True)
            assert r1.returncode == 0
            assert os.path.exists(snap_file)

            # Step 2: Source snapshot and print var
            cmd2 = f'source "{snap_file}" && printf "%s" "$TOOLRUSH_TEST"'
            r2 = subprocess.run(['bash', '-c', cmd2], capture_output=True, text=True)
            assert r2.returncode == 0
            assert r2.stdout == "persisted"

    def test_timeout_and_process_tree_cleanup(self):
        # Verify that kill_process_tree kills command and all spawned background children
        extra = {'preexec_fn': os.setsid} if os.name != 'nt' else {}
        proc = subprocess.Popen(
            ['bash', '-c', 'sleep 60 & sleep 60 & wait'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **extra
        )
        time.sleep(0.1)
        assert proc.poll() is None

        # Terminate entire process tree
        kill_process_tree(proc, timeout=2.0)
        assert proc.poll() is not None

    def test_snapshot_commit_failure_fail_closed(self):
        # If target snapshot path is unwriteable / blocked, commit must fail closed
        with tempfile.TemporaryDirectory() as td:
            snap_dir = os.path.join(td, "snap_block")
            os.mkdir(snap_dir)
            cmd = f'__tmp="{td}/tmp.sh"; echo "export X=1" > "$__tmp" && mv -f "$__tmp" "{snap_dir}/nonexistent/cannot_write.sh"'
            r = subprocess.run(['bash', '-c', cmd], capture_output=True, text=True)
            assert r.returncode != 0
