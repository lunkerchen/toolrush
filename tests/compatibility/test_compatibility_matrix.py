"""ToolRush Compatibility and Degradation Tests.

Tests payload integrity, helper verification, hash gates, and fail-closed behaviors.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
import pytest

P = Path(__file__).resolve().parents[2] / 'v2' / 'plugin'


def _real_hermes_root():
    """Path of a real hermes-agent install, or None. Mirrors doctor.py discovery."""
    for candidate in (os.environ.get('HERMES_ROOT'),
                      Path.home() / '.hermes' / 'hermes-agent',
                      P.parent.parent / 'hermes-agent'):
        if candidate and (Path(candidate) / 'hermes_cli').is_dir():
            return str(Path(candidate).resolve())
    try:
        import hermes_cli
    except ImportError:
        return None
    # conftest may have registered a stub hermes_cli; a stub is not an install.
    module_file = getattr(hermes_cli, '__file__', None)
    if not module_file:
        return None
    return str(Path(module_file).parent.parent.resolve())


def _isolated_env(tmp_path, **overrides):
    """os.environ minus every hermes/python path leak, with HOME under tmp_path."""
    env = {k: v for k, v in os.environ.items()
           if k not in ('HOME', 'HERMES_ROOT', 'HERMES_HOME', 'PYTHONPATH')}
    env['HOME'] = str(tmp_path)
    env['HERMES_HOME'] = str(tmp_path / '.hermes')
    env.update({k: v for k, v in overrides.items() if v is not None})
    return env


def _run_doctor(env, *args, plugin_dir=P, python_flags=(), timeout=60):
    return subprocess.run(
        [sys.executable, *python_flags, str(Path(plugin_dir) / 'doctor.py'), *args],
        capture_output=True, text=True, env=env, timeout=timeout,
    )


class TestCompatibilityPayload:
    @pytest.fixture
    def compat(self):
        spec = importlib.util.spec_from_file_location('compat', P / 'compat.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.fixture
    def payload_data(self):
        return json.loads((P / 'payload.json').read_text(encoding='utf-8'))

    def test_payload_python_version_gate(self, payload_data):
        # Payload explicitly targets Python 3.11 for Windows function bytecode patches
        assert payload_data.get('python') == [3, 11]

    def test_all_helper_blobs_match_checksums(self, compat, payload_data):
        for name, row in payload_data['helpers'].items():
            content = (P / row['file']).read_bytes()
            # verify_blob must not raise
            compat.verify_blob(content, row['sha256'])

    def test_tampered_helper_fails_closed(self, compat):
        fake_content = b"print('tampered')"
        fake_hash = hashlib.sha256(b"original").hexdigest()
        with pytest.raises(RuntimeError, match="ToolRush payload hash mismatch"):
            compat.verify_blob(fake_content, fake_hash)

    def test_unknown_function_patch_fails_closed(self, compat):
        bad_row = {
            'module': 'math',
            'qualname': 'non_existent_func_xyz',
            'before': 'def foo(): pass',
            'after': 'def foo(): return 42'
        }
        with pytest.raises(Exception):
            compat.prepare_rows([bad_row])

    def test_doctor_smoke_in_isolated_process(self, tmp_path, need_hermes_install):
        # Run doctor --smoke as a clean subprocess with isolated HOME and explicit HERMES_ROOT
        hermes_root = _real_hermes_root()
        env = _isolated_env(tmp_path, HERMES_ROOT=hermes_root)
        res = _run_doctor(env, '--smoke')
        assert res.returncode == 0, f"Doctor failed: {res.stdout}\n{res.stderr}"
        doc = json.loads(res.stdout)
        assert doc['ok'] is True
        assert doc['toolrush_version'] == '2.1.0'
        assert 'warm_shell' in doc['lanes']

    def test_doctor_fails_closed_when_hermes_missing(self, tmp_path):
        # Hermes missing entirely from environment: doctor must fail closed with exit 2 and ok=False
        env = _isolated_env(tmp_path)
        res = _run_doctor(env, python_flags=['-S'])
        assert res.returncode == 2, f"Expected exit 2 when Hermes missing, got {res.returncode}: {res.stdout}"
        doc = json.loads(res.stdout)
        assert doc['ok'] is False
        assert doc.get('hermes_status') == 'missing'

    def test_doctor_degraded_when_bash_missing(self, tmp_path, need_posix_lanes):
        # Missing bash executable
        hermes_root = _real_hermes_root()
        env = _isolated_env(tmp_path, HERMES_ROOT=hermes_root, PATH='/nonexistent_path_no_bin')
        res = _run_doctor(env)
        assert res.returncode == 2
        doc = json.loads(res.stdout)
        assert doc['ok'] is False
        assert doc['lanes']['warm_shell']['status'] == 'degraded'
        assert doc['lanes']['warm_shell']['reason'] == 'bash executable not found'
