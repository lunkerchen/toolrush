#!/usr/bin/env python3
"""Build a deterministic release zip plus SHA256SUMS and MANIFEST.json.

Usage:
    python3 scripts/package_release.py --version 2.1.0 [--output-dir dist] [--source-dir v2/plugin]

Determinism: fixed timestamps, sorted walk, normalized permissions. Two runs
over identical sources produce byte-identical zips.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import zipfile
from pathlib import Path

FIXED_TIME = (2026, 1, 1, 0, 0, 0)
# Release versions are strict semver core with an optional prerelease suffix.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$")
EXCLUDED_DIRS = {"__pycache__", ".git", ".github", ".pytest_cache", ".mypy_cache", ".venv", "node_modules"}
EXCLUDED_FILES = {".DS_Store"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo")


def source_version(source_dir: Path) -> str:
    """Read VERSION from the packaged tree's own version.py (single source of truth)."""
    version_file = source_dir / "version.py"
    if not version_file.is_file():
        raise ValueError(f"no version.py in source dir: {source_dir}")
    spec = importlib.util.spec_from_file_location("toolrush_packaged_version", version_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = getattr(module, "VERSION", None)
    if not isinstance(version, str):
        raise ValueError(f"version.py in {source_dir} defines no VERSION string")
    return version


def iter_files(source_dir: Path):
    """Yield (absolute path, archive-relative path) in deterministic order."""
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for name in sorted(files):
            if name in EXCLUDED_FILES or name.endswith(EXCLUDED_SUFFIXES):
                continue
            full = Path(root) / name
            if full.is_symlink() or not full.is_file():
                continue
            yield full, full.relative_to(source_dir).as_posix()


def build_zip(source_dir: Path, zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for full, rel in iter_files(source_dir):
            mode = 0o755 if full.stat().st_mode & stat.S_IXUSR else 0o644
            zinfo = zipfile.ZipInfo(rel, date_time=FIXED_TIME)
            # S_IFREG must be present: install.sh rejects members whose mode is
            # not a regular file or directory, so a bare permission mode would
            # make every artifact we build unextractable.
            zinfo.external_attr = (stat.S_IFREG | mode) << 16
            zinfo.compress_type = zipfile.ZIP_DEFLATED
            zinfo.create_system = 3  # Unix, so permissions survive the round trip
            zf.writestr(zinfo, full.read_bytes())
    return hashlib.sha256(zip_path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Package a deterministic toolrush release artifact.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--source-dir", default="v2/plugin")
    args = parser.parse_args()

    version = args.version[1:] if args.version.startswith("v") else args.version
    if not VERSION_RE.match(version):
        parser.error(f"invalid version format: {args.version!r} (expected MAJOR.MINOR.PATCH[-prerelease])")

    source_dir = Path(args.source_dir).resolve()
    if not source_dir.is_dir():
        parser.error(f"source dir not found: {source_dir}")

    # Tag gate: refuse to label an artifact with a version the code does not claim.
    try:
        declared = source_version(source_dir)
    except ValueError as exc:
        parser.error(str(exc))
    if declared != version:
        parser.error(f"version mismatch: requested {version} but {source_dir}/version.py declares {declared}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact = f"toolrush-{version}.zip"
    zip_path = output_dir / artifact
    digest = build_zip(source_dir, zip_path)

    (output_dir / "SHA256SUMS").write_text(f"{digest}  {artifact}\n")
    (output_dir / "MANIFEST.json").write_text(
        json.dumps(
            {"version": version, "tag": f"v{version}", "artifact": artifact, "sha256": digest},
            indent=2,
        )
        + "\n"
    )

    print(f"{artifact}  {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
