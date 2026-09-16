"""Test deterministic packaging and version gate verification."""
import importlib.util
import hashlib
import os
from pathlib import Path
import tempfile
import zipfile
import pytest

vs_path = Path(__file__).resolve().parents[2] / 'v2' / 'plugin' / 'version.py'
spec = importlib.util.spec_from_file_location('toolrush_ver', vs_path)
ver_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ver_mod)
VERSION = ver_mod.VERSION

def test_single_version_truth_matches_plugin_yaml():
    yaml_path = Path(__file__).resolve().parents[2] / 'v2' / 'plugin' / 'plugin.yaml'
    lines = yaml_path.read_text().splitlines()
    version_line = next((l for l in lines if l.startswith('version:')), None)
    assert version_line is not None
    extracted = version_line.split(':', 1)[1].strip()
    assert extracted == VERSION, f"plugin.yaml version {extracted} != version.py {VERSION}"

def test_deterministic_packaging_hash_reproducibility():
    fixed_time = (2026, 1, 1, 0, 0, 0)
    plugin_dir = Path(__file__).resolve().parents[2] / 'v2' / 'plugin'

    def create_zip(out_path):
        with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(plugin_dir):
                dirs.sort()
                for file in sorted(files):
                    if file.endswith('.pyc') or '__pycache__' in root:
                        continue
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, plugin_dir)
                    zinfo = zipfile.ZipInfo(rel_path, date_time=fixed_time)
                    zinfo.external_attr = 0o644 << 16
                    with open(full_path, 'rb') as f:
                        zf.writestr(zinfo, f.read())

    with tempfile.TemporaryDirectory() as td:
        z1 = os.path.join(td, "pack1.zip")
        z2 = os.path.join(td, "pack2.zip")
        create_zip(z1)
        create_zip(z2)
        h1 = hashlib.sha256(open(z1, 'rb').read()).hexdigest()
        h2 = hashlib.sha256(open(z2, 'rb').read()).hexdigest()
        assert h1 == h2, "Packaging is non-deterministic"
