"""Release packaging contract: single version truth, deterministic zip, valid artifacts."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "v2" / "plugin"
SCRIPT = REPO_ROOT / "scripts" / "package_release.py"


def test_single_version_truth_matches_plugin_yaml(package_release):
    version = package_release.source_version(PLUGIN_DIR)
    lines = (PLUGIN_DIR / "plugin.yaml").read_text().splitlines()
    version_line = next((l for l in lines if l.startswith("version:")), None)
    assert version_line is not None
    extracted = version_line.split(":", 1)[1].strip()
    assert extracted == version, f"plugin.yaml version {extracted} != version.py {version}"


def test_build_zip_is_deterministic(package_release, tmp_path):
    """Two builds over identical sources must be byte-identical."""
    h1 = package_release.build_zip(PLUGIN_DIR, tmp_path / "pack1.zip")
    h2 = package_release.build_zip(PLUGIN_DIR, tmp_path / "pack2.zip")
    assert h1 == h2, "packaging is non-deterministic"
    assert h1 == hashlib.sha256((tmp_path / "pack1.zip").read_bytes()).hexdigest()


def test_package_release_emits_valid_sha256sums_and_manifest(package_release, tmp_path):
    version = package_release.source_version(PLUGIN_DIR)
    out = tmp_path / "dist"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--version", version, "--output-dir", str(out)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr

    artifact = f"toolrush-{version}.zip"
    zip_path = out / artifact
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    sums = (out / "SHA256SUMS").read_text()
    assert sums == f"{digest}  {artifact}\n"

    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert manifest == {
        "version": version,
        "tag": f"v{version}",
        "artifact": artifact,
        "sha256": digest,
    }


def test_version_gate_rejects_mismatched_tag(package_release, tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--version", "99.99.99", "--output-dir", str(tmp_path)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "version mismatch" in proc.stderr
