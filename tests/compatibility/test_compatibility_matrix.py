"""ToolRush Compatibility and Degradation Tests.

Tests payload integrity, helper verification, hash gates, and fail-closed behaviors.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

P = Path(__file__).resolve().parents[2] / 'v2' / 'plugin'


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

    def test_doctor_smoke_in_isolated_process(self):
        import subprocess
        import sys
        # Run doctor --smoke as a clean subprocess
        res = subprocess.run(
            [sys.executable, str(P / 'doctor.py'), '--smoke'],
            capture_output=True,
            text=True,
            timeout=10
        )
        assert res.returncode == 0, f"Doctor failed: {res.stdout}\n{res.stderr}"
        doc = json.loads(res.stdout)
        assert doc['ok'] is True
        assert doc['toolrush_version'] == '2.1.0'
        assert 'warm_shell' in doc['lanes']
