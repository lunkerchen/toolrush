"""Cross-platform Read-only ToolRush installed integrity and compatibility check. No models."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

P = Path(__file__).parent
# Single source of truth for version
vs = importlib.util.spec_from_file_location('toolrush_doctor_version', P / 'version.py')
vm = importlib.util.module_from_spec(vs)
vs.loader.exec_module(vm)
VERSION = vm.VERSION

parser = argparse.ArgumentParser(description="ToolRush cross-platform doctor")
parser.add_argument('--smoke', action='store_true', help="Run in-process smoke test")
args = parser.parse_args()

# Discover platform & python
is_win = sys.platform == 'win32'
platform_name = 'windows' if is_win else ('darwin' if sys.platform == 'darwin' else 'linux')

# Discover executables
bash_path = shutil.which('bash') or ('bash' if not is_win else None)
rg_path = shutil.which('rg')
rg_ver = None
if rg_path:
    try:
        res = subprocess.run([rg_path, '--version'], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            rg_ver = res.stdout.splitlines()[0].strip()
    except Exception:
        pass

# Discover Hermes package/root
hermes_root = None
hermes_ver = None
for candidate in [
    os.environ.get('HERMES_ROOT'),
    str(Path.home() / '.hermes' / 'hermes-agent'),
    str(P.parent.parent / 'hermes-agent'),
]:
    if candidate and Path(candidate).is_dir() and (Path(candidate) / 'hermes_cli').is_dir():
        hermes_root = str(Path(candidate).resolve())
        break

if not hermes_root:
    try:
        import hermes_cli
        hermes_root = str(Path(hermes_cli.__file__).parent.parent.resolve())
    except Exception:
        pass

if hermes_root and hermes_root not in sys.path:
    sys.path.insert(0, hermes_root)

if hermes_root:
    try:
        import hermes_cli
        hermes_ver = getattr(hermes_cli, '__version__', None)
    except Exception:
        pass

s = importlib.util.spec_from_file_location('toolrush_doctor_compat', P / 'compat.py')
c = importlib.util.module_from_spec(s)
s.loader.exec_module(c)

payload = json.loads((P / 'payload.json').read_text())

result = {
    'ok': False,
    'platform': platform_name,
    'toolrush_version': VERSION,
    'python': sys.version.split()[0],
    'hermes_root': hermes_root,
    'hermes_version': hermes_ver,
    'executables': {
        'bash': bash_path,
        'rg': rg_path,
        'rg_version': rg_ver,
    },
    'payload': {},
    'lanes': {},
    'restart_note': 'This fresh-process check does not activate already-running gateways.',
}

try:
    # Check plugin.yaml version consistency
    yaml_version = next((l.split(':', 1)[1].strip() for l in (P / 'plugin.yaml').read_text().splitlines() if l.startswith('version:')), None)
    assert yaml_version == VERSION, f"plugin.yaml version '{yaml_version}' != version.py '{VERSION}'"

    # Verify helper files
    for name, row in payload['helpers'].items():
        c.verify_blob((P / row['file']).read_bytes(), row['sha256'])
        result['payload'][name] = 'verified'

    # Unified lane schema
    if is_win:
        for lane, rows in payload['lanes'].items():
            try:
                pending = len(c.prepare_rows(rows))
                result['lanes'][lane] = {'status': 'compatible', 'provider': 'toolrush', 'pending_function_patches': pending}
            except Exception as exc:
                result['lanes'][lane] = {'status': 'degraded', 'provider': 'toolrush', 'reason': str(exc)}
        lanes_ok = all(v['status'] == 'compatible' for v in result['lanes'].values())
    else:
        # On macOS/POSIX:
        # - warm_shell is active & ready (provider: toolrush)
        # - native_read and native_search are handled by Hermes upstream
        # - parallel_rpc & snapshot patches are Windows-only; POSIX uses native / fallback
        warm_status = 'ready' if bash_path else 'degraded'
        result['lanes']['warm_shell'] = {
            'status': warm_status,
            'provider': 'toolrush',
            'reason': None if warm_status == 'ready' else 'bash executable not found'
        }
        result['lanes']['native_read'] = {
            'status': 'ready' if hermes_root else 'unverified_clean_environment',
            'provider': 'upstream',
            'reason': None if hermes_root else 'hermes_root not present in clean environment'
        }
        result['lanes']['native_search'] = {
            'status': 'ready' if rg_path else 'degraded',
            'provider': 'upstream',
            'reason': None if rg_path else 'rg executable not found'
        }
        result['lanes']['parallel_rpc'] = {
            'status': 'ready',
            'provider': 'upstream_sequential_or_worker',
        }
        lanes_ok = result['lanes']['warm_shell']['status'] == 'ready'

    if args.smoke:
        # Cross-platform smoke test: verify plugin load & fresh subprocess lanes without model/user writes
        boot_status = {'status': 'unverified', 'version': VERSION}
        if hermes_root:
            try:
                from hermes_cli.plugins import PluginManager, PluginManifest
                manager = PluginManager()
                manager._load_plugin(PluginManifest(name='toolrush', version=VERSION, source='user', path=str(P), key='toolrush'))
                loaded = manager._plugins.get('toolrush')
                assert loaded and loaded.enabled and not loaded.error, f"Plugin failed to load: {getattr(loaded, 'error', None)}"
                boot_status = {'status': 'ready', 'version': VERSION, 'mode': 'hermes_cli'}
            except Exception as exc:
                boot_status = {'status': 'degraded', 'version': VERSION, 'reason': f"hermes_cli plugin loader failed: {exc}"}
        else:
            # In clean environments without hermes_cli installed, label clearly rather than forcing success
            try:
                spec = importlib.util.spec_from_file_location('toolrush', P / '__init__.py')
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    boot_status = {'status': 'clean_environment_rejection_or_direct', 'version': VERSION, 'hermes_installed': False}
                else:
                    boot_status = {'status': 'degraded', 'version': VERSION, 'reason': 'Failed to resolve module spec'}
            except Exception as exc:
                boot_status = {'status': 'degraded', 'version': VERSION, 'reason': str(exc)}
        result['boot'] = boot_status

    result['ok'] = lanes_ok
except Exception as exc:
    result['ok'] = False
    result['error'] = str(exc)

print(json.dumps(result, indent=2))
sys.exit(0 if result['ok'] else 2)
