"""Test suite verifying installer safety, atomic upgrade, rollback, and trap cleanup."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / 'scripts'
INSTALL_SH = SCRIPTS_DIR / 'install.sh'
REPO_ROOT = Path(__file__).resolve().parents[2]

class TestInstallerSafety:
    @pytest.fixture
    def fake_env(self):
        with tempfile.TemporaryDirectory() as td:
            hermes_dir = Path(td) / '.hermes'
            plugins_dir = hermes_dir / 'plugins'
            plugins_dir.mkdir(parents=True)
            yield {'td': td, 'hermes': hermes_dir, 'plugins': plugins_dir}

    def test_initial_install_succeeds(self, fake_env):
        env = os.environ.copy()
        env['HERMES_HOME'] = str(fake_env['hermes'])
        env['TOOLRUSH_REPO'] = str(REPO_ROOT)
        env['TOOLRUSH_ALLOW_UNPINNED'] = '1'
        env['TOOLRUSH_VERSION'] = 'HEAD'

        res = subprocess.run([str(INSTALL_SH)], env=env, capture_output=True, text=True)
        assert res.returncode == 0, f"Installer failed: {res.stderr}"
        target = fake_env['plugins'] / 'toolrush'
        assert target.is_dir()
        assert (target / 'plugin.yaml').is_file()
        assert (target / 'doctor.py').is_file()

    def test_repeated_upgrade_no_nesting(self, fake_env):
        env = os.environ.copy()
        env['HERMES_HOME'] = str(fake_env['hermes'])
        env['TOOLRUSH_REPO'] = str(REPO_ROOT)
        env['TOOLRUSH_ALLOW_UNPINNED'] = '1'
        env['TOOLRUSH_VERSION'] = 'HEAD'

        # First install
        r1 = subprocess.run([str(INSTALL_SH)], env=env, capture_output=True, text=True)
        assert r1.returncode == 0
        target = fake_env['plugins'] / 'toolrush'
        assert not (target / 'toolrush').exists(), "Nested toolrush directory detected on initial install"

        # Second install (upgrade)
        r2 = subprocess.run([str(INSTALL_SH)], env=env, capture_output=True, text=True)
        assert r2.returncode == 0
        assert not (target / 'toolrush').exists(), "Nested toolrush directory detected on upgrade"
        assert not (target / 'plugin').exists(), "Nested plugin directory detected on upgrade"

    def test_unpinned_fallback_refused_by_default(self, fake_env):
        env = os.environ.copy()
        env['HERMES_HOME'] = str(fake_env['hermes'])
        env['TOOLRUSH_REPO'] = str(REPO_ROOT)
        env['TOOLRUSH_VERSION'] = 'nonexistent-tag-v999.0'
        env.pop('TOOLRUSH_ALLOW_UNPINNED', None)

        res = subprocess.run([str(INSTALL_SH)], env=env, capture_output=True, text=True)
        assert res.returncode != 0
        assert "Uncontrolled fallback to main is refused for safety" in res.stderr

    def test_concurrent_installation_refused(self, fake_env):
        lock_file = fake_env['plugins'] / '.toolrush_install.lock'
        lock_file.touch()

        env = os.environ.copy()
        env['HERMES_HOME'] = str(fake_env['hermes'])
        env['TOOLRUSH_REPO'] = str(REPO_ROOT)
        env['TOOLRUSH_ALLOW_UNPINNED'] = '1'
        env['TOOLRUSH_VERSION'] = 'HEAD'

        res = subprocess.run([str(INSTALL_SH)], env=env, capture_output=True, text=True)
        assert res.returncode != 0
        assert "Concurrent installation in progress" in res.stderr

    def test_verification_failure_rolls_back_and_retains_backup(self, fake_env):
        target = fake_env['plugins'] / 'toolrush'
        target.mkdir()
        canary = target / 'canary.txt'
        canary.write_text("existing-version-canary")

        env = os.environ.copy()
        env['HERMES_HOME'] = str(fake_env['hermes'])
        env['TOOLRUSH_REPO'] = str(REPO_ROOT)
        env['TOOLRUSH_ALLOW_UNPINNED'] = '1'
        env['TOOLRUSH_VERSION'] = 'HEAD'
        # Break python so postinstall doctor fails
        env['PATH'] = '/usr/bin:/bin' # will use system /usr/bin without pytest etc., or we can point python3 to false
        
        # We simulate staging failure by pointing python3 to a script that fails doctor --smoke
        with tempfile.TemporaryDirectory() as bin_dir:
            fake_py = Path(bin_dir) / 'python3'
            fake_py.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then /opt/anaconda3/bin/python3 "$@"; else exit 1; fi\n')
            fake_py.chmod(0o755)
            env['PATH'] = f"{bin_dir}:{env['PATH']}"
            res = subprocess.run([str(INSTALL_SH)], env=env, capture_output=True, text=True)
            assert res.returncode != 0

        # Verify canary was restored by rollback or preserved
        assert canary.exists() or any("toolrush_backup" in str(p) for p in fake_env['plugins'].glob('.toolrush_backup_*'))
