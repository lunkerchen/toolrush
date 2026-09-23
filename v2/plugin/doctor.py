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
parser.add_argument('--hermes-root', help="hermes-agent source root (default: $HERMES_ROOT, ~/.hermes/hermes-agent)")
args = parser.parse_args()

# Discover platform & python
is_win = sys.platform == 'win32'
platform_name = 'windows' if is_win else ('darwin' if sys.platform == 'darwin' else 'linux')

# Discover executables
bash_path = shutil.which('bash')
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
    args.hermes_root,
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

# Hermes runs under its own venv; lane results from a different interpreter
# (bytecode payload is pinned to one minor version) are not authoritative.
hermes_python = None
if hermes_root:
    venv_py = Path(hermes_root) / 'venv' / ('Scripts/python.exe' if is_win else 'bin/python')
    if venv_py.exists():
        try:
            out = subprocess.run([str(venv_py), '-c', 'import sys;print("%d.%d" % sys.version_info[:2])'],
                                 capture_output=True, text=True, timeout=5)
            hermes_python = {'executable': str(venv_py), 'version': out.stdout.strip() or None}
        except Exception:
            hermes_python = {'executable': str(venv_py), 'version': None}


def _upstream_has(rel, needle):
    # ponytail: source-text probe, not an import -- doctor may run under a
    # different interpreter than Hermes. Upgrade to an import probe run via
    # hermes_python if upstream starts generating these symbols dynamically.
    try:
        return needle in (Path(hermes_root) / rel).read_text(encoding='utf-8')
    except OSError:
        return False


s = importlib.util.spec_from_file_location('toolrush_doctor_compat', P / 'compat.py')
c = importlib.util.module_from_spec(s)
s.loader.exec_module(c)

payload = json.loads((P / 'payload.json').read_text())

result = {
    'ok': False,
    'platform': platform_name,
    'toolrush_version': VERSION,
    'python': sys.version.split()[0],
    'python_executable': sys.executable,
    'hermes_root': hermes_root,
    'hermes_version': hermes_ver,
    'hermes_python': hermes_python,
    'executables': {
        'bash': bash_path,
        'rg': rg_path,
        'rg_version': rg_ver,
    },
    'payload': {},
    'lanes': {},
    'restart_note': 'This fresh-process check does not activate already-running gateways.',
}

if not hermes_root:
    result['hermes_status'] = 'missing'

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
        if tuple(payload.get('python', [])) != sys.version_info[:2]:
            lanes_ok = False
            for lane in payload['lanes']:
                result['lanes'][lane] = {'status': 'degraded', 'provider': 'toolrush', 'reason': 'python version mismatch'}
        else:
            for lane, rows in payload['lanes'].items():
                try:
                    pending = len(c.prepare_rows(rows))
                    result['lanes'][lane] = {'status': 'compatible', 'provider': 'toolrush', 'pending_function_patches': pending}
                except Exception as exc:
                    result['lanes'][lane] = {'status': 'degraded', 'provider': 'toolrush', 'reason': str(exc)}
            lanes_ok = all(v['status'] == 'compatible' for v in result['lanes'].values())
    else:
        # On macOS/POSIX ToolRush provides only the warm-shell lane. Read,
        # search and parallel RPC are whatever upstream Hermes ships; report
        # what is actually present instead of claiming them.
        warm_status = 'ready' if bash_path else 'degraded'
        result['lanes']['warm_shell'] = {
            'status': warm_status,
            'provider': 'toolrush',
            'reason': None if warm_status == 'ready' else 'bash executable not found'
        }
        if hermes_root:
            probes = {
                'native_read': (_upstream_has('tools/file_operations.py', 'def _read_file_native'),
                                'upstream ShellFileOperations._read_file_native not found'),
                'native_search': (bool(rg_path), 'rg executable not found'),
                'parallel_rpc': (_upstream_has('tools/code_execution_tool.py', 'def parallel(')
                                 or _upstream_has('tools/code_kernel.py', 'def parallel('),
                                 'upstream hermes_tools has no parallel(); ToolRush ships it on Windows only'),
            }
            result['upstream'] = {k: {'status': 'present' if ok else 'unavailable', 'reason': None if ok else why}
                                  for k, (ok, why) in probes.items()}
        if hermes_python and hermes_python['version'] != '%d.%d' % sys.version_info[:2]:
            result['warnings'] = [f"doctor ran on Python {sys.version_info[0]}.{sys.version_info[1]}, "
                                  f"Hermes runs {hermes_python['version']}; rerun with {hermes_python['executable']}"]
        lanes_ok = bool(bash_path and warm_status == 'ready')

    if args.smoke:
        # Cross-platform smoke test: verify plugin load & fresh subprocess lanes without model/user writes
        boot_status = {'status': 'unverified', 'version': VERSION}

        # Genuine warm-shell execution through the real broker: clean env, isolated cwd
        smoke_cmd_ok = False
        if bash_path and hermes_root:
            try:
                import tempfile
                c.load_helpers(payload)  # hash-verified; registers tools.* under the real package
                from tools.toolrush_shell import WarmHandle, WarmShell, build_frame
                with tempfile.TemporaryDirectory() as td:
                    local = type('L', (), {'_IS_WINDOWS': is_win, '_find_bash': lambda s: bash_path,
                        '_make_run_env': lambda s, e: {'PATH': os.environ.get('PATH', '')}, '_resolve_safe_cwd': lambda s, c: td})()
                    owner = type('O', (), {'env': {}, 'cwd': td})()
                    shell = WarmShell(local, owner)
                    shell.lock.acquire()  # WarmHandle releases it when the frame ends
                    h = WarmHandle(shell, *build_frame(owner, local, 'echo toolrush-smoke-ok'))
                    out = h.stdout.read()
                    smoke_cmd_ok = (h.wait(10) == 0 and b'toolrush-smoke-ok' in out)
                    shell.close()
            except Exception:
                smoke_cmd_ok = False

        if hermes_root:
            try:
                from hermes_cli.plugins import PluginManager, PluginManifest
                manager = PluginManager()
                manager._load_plugin(PluginManifest(name='toolrush', version=VERSION, source='user', path=str(P), key='toolrush'))
                loaded = manager._plugins.get('toolrush')
                assert loaded and loaded.enabled and not loaded.error, f"Plugin failed to load: {getattr(loaded, 'error', None)}"
                compat_status = (getattr(sys.modules.get('hermes_cli.plugins'), '_COMPAT_STATUS', None)
                                 or getattr(sys.modules.get('toolrush'), '_COMPAT_STATUS', None))
                if compat_status:
                    result['compat_status'] = compat_status
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
                    boot_status = {'status': 'clean_environment_direct_load', 'version': VERSION, 'hermes_installed': False}
                else:
                    boot_status = {'status': 'degraded', 'version': VERSION, 'reason': 'Failed to resolve module spec'}
            except Exception as exc:
                boot_status = {'status': 'degraded', 'version': VERSION, 'reason': str(exc)}
        result['boot'] = boot_status
        result['smoke_execution'] = {'warm_shell': 'passed' if smoke_cmd_ok else 'failed'}
        # Fail closed without Hermes: a direct module load is not a working install.
        result['ok'] = bool(hermes_root and lanes_ok and boot_status.get('status') == 'ready' and smoke_cmd_ok)
    else:
        result['ok'] = bool(hermes_root and lanes_ok)
except Exception as exc:
    result['ok'] = False
    result['error'] = str(exc)

print(json.dumps(result, indent=2))
sys.exit(0 if result['ok'] else 2)
