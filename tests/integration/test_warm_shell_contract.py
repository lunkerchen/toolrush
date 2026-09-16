"""Contract tests for ToolRush warm-shell implementation.

Drives ``tools.toolrush_shell`` directly:
- ``build_frame``: delimiter isolation, env filtering, mktemp snapshot clause rewrite.
- ``WarmShell`` & ``WarmHandle``: live subprocess broker execution, exit code extraction,
  streaming output capture, and process tree termination via ``kill_process_tree``.
"""
import os
import shutil
import tempfile
from pathlib import Path
import pytest

P = Path(__file__).resolve().parents[2] / 'v2' / 'plugin'


class FakeLocal:
    _IS_WINDOWS = False

    def _find_bash(self):
        bash = shutil.which('bash')
        if not bash:
            raise RuntimeError("bash not found")
        return bash

    def _make_run_env(self, env):
        return {k: v for k, v in env.items() if isinstance(v, str)}

    def _resolve_safe_cwd(self, cwd):
        return cwd or os.getcwd()


class FakeOwner:
    def __init__(self, cwd, snap_path):
        self.cwd = cwd
        self.env = {'TOOLRUSH_VAR': 'persisted_val', 'PATH': os.environ.get('PATH', '')}
        self._snapshot_path = snap_path

    def _quote_shell_path(self, path):
        return f"'{path}'"

    def _snapshot_excluded_passthrough_names(self):
        return ['SECRET_KEY', 'AWS_SECRET']


@pytest.fixture
def fake_env():
    with tempfile.TemporaryDirectory() as td:
        snap_path = os.path.join(td, "session_snap.sh")
        local = FakeLocal()
        owner = FakeOwner(td, snap_path)
        yield {'td': td, 'snap_path': snap_path, 'local': local, 'owner': owner}


class TestWarmShellBuildFrame:
    def test_build_frame_delimiters_and_exports(self, toolrush_shell, fake_env):
        build_frame = toolrush_shell.build_frame
        command = "echo 'inside-frame'"
        frame_bytes, begin, end, commit = build_frame(fake_env['owner'], fake_env['local'], command)

        assert b'__TRB_' in begin
        assert b'__TRE_' in end
        assert b"export TOOLRUSH_VAR=persisted_val" in frame_bytes
        assert b"echo 'inside-frame'" in frame_bytes
        assert commit is None  # no snapshot allocation line in command

    def test_build_frame_snapshot_rewrite(self, toolrush_shell, fake_env):
        build_frame = toolrush_shell.build_frame
        snap_path = fake_env['snap_path']
        move = f"&& mv -f \"$__hermes_snap_tmp\" '{snap_path}';"
        snap_clause = f"__hermes_snap_tmp=$(mktemp -t snap.XXXXXX) && export -p > \"$__hermes_snap_tmp\" {move}"
        command = f"echo 'test'\n{snap_clause}"

        frame_bytes, begin, end, commit = build_frame(fake_env['owner'], fake_env['local'], command)

        assert commit is not None
        temporary, target, ready = commit
        assert target == snap_path
        assert temporary.startswith(snap_path + ".tmp.")
        assert ready == temporary + ".ready"
        assert f": > '{ready}';".encode() in frame_bytes


class TestWarmShellExecution:
    def test_warmshell_execution_and_streaming(self, toolrush_shell, fake_env):
        WarmShell = toolrush_shell.WarmShell
        WarmHandle = toolrush_shell.WarmHandle
        build_frame = toolrush_shell.build_frame

        shell = WarmShell(fake_env['local'], fake_env['owner'])
        try:
            cmd = "echo 'line 1'; echo 'line 2'; exit 0"
            frame, begin, end, commit = build_frame(fake_env['owner'], fake_env['local'], cmd)
            shell.lock.acquire()
            handle = WarmHandle(shell, frame, begin, end, commit)
            out = handle.stdout.read().decode('utf-8')
            rc = handle.wait(timeout=5)

            assert rc == 0
            assert "line 1" in out
            assert "line 2" in out
        finally:
            shell.close()

    def test_warmshell_exit_code_propagation(self, toolrush_shell, fake_env):
        WarmShell = toolrush_shell.WarmShell
        WarmHandle = toolrush_shell.WarmHandle
        build_frame = toolrush_shell.build_frame

        shell = WarmShell(fake_env['local'], fake_env['owner'])
        try:
            cmd = "echo 'error occurred' >&2; exit 42"
            frame, begin, end, commit = build_frame(fake_env['owner'], fake_env['local'], cmd)
            shell.lock.acquire()
            handle = WarmHandle(shell, frame, begin, end, commit)
            out = handle.stdout.read().decode('utf-8')
            rc = handle.wait(timeout=5)

            assert rc == 42
            assert "error occurred" in out
        finally:
            shell.close()

    def test_warmshell_process_tree_cleanup(self, toolrush_shell, fake_env):
        WarmShell = toolrush_shell.WarmShell

        shell = WarmShell(fake_env['local'], fake_env['owner'])
        assert shell.proc.poll() is None

        # Terminate shell and verify process tree cleanup
        shell.close()
        assert shell.dead is True
        assert shell.proc.poll() is not None
