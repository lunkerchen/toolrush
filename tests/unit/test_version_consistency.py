"""Test single source of version truth across ToolRush assets."""
import importlib.util
from pathlib import Path

P = Path(__file__).resolve().parents[2] / 'v2' / 'plugin'
ROOT = Path(__file__).resolve().parents[2]


def test_version_single_source_of_truth():
    spec = importlib.util.spec_from_file_location('v', P / 'version.py')
    vm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vm)
    version = vm.VERSION

    # Check plugin.yaml
    yaml_text = (P / 'plugin.yaml').read_text(encoding='utf-8')
    yaml_version = next((l.split(':', 1)[1].strip() for l in yaml_text.splitlines() if l.startswith('version:')), None)
    assert yaml_version == version, f"plugin.yaml version {yaml_version} != {version}"

    # Check doctor.py reports exact version
    import subprocess
    import sys
    res = subprocess.run([sys.executable, str(P / 'doctor.py')], capture_output=True, text=True, timeout=5)
    import json
    doc = json.loads(res.stdout)
    assert doc['toolrush_version'] == version
