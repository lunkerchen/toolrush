#!/usr/bin/env python3
"""Assert doctor.py's contract against a JSON report + its exit code.

doctor.py is deliberately fail-closed: it reports ok=false and exits 2 when no
Hermes install is present (the warm-shell broker, native read/search and RPC
lanes all live in the Hermes tree). A CI runner has no Hermes, so demanding
exit 0 there is unsatisfiable. What CI *can* and *must* check is that doctor is
internally consistent and that the hash-pinned payload always verifies -- that
last part is what catches a CRLF-corrupted checkout, which otherwise exits 2 for
the wrong reason and looks like a pass.

Usage: verify_doctor_contract.py <doctor.json> <exit_code> [--smoke]
"""
import argparse
import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / 'v2' / 'plugin'
EXIT_OK = 0
EXIT_FAIL_CLOSED = 2
OK_LANE_STATES = ('ready', 'compatible')
OK_BOOT_STATES = ('ready', 'clean_environment_direct_load')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('report')
    ap.add_argument('exit_code', type=int)
    ap.add_argument('--smoke', action='store_true')
    args = ap.parse_args()

    raw = Path(args.report).read_text(encoding='utf-8', errors='replace')
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(raw[:4000])
        print(f'doctor contract: FAILED (report is not JSON: {exc})')
        return 1

    problems = []

    # 1. Doctor must identify the machine it actually ran on.
    expected_platform = 'windows' if os.name == 'nt' else ('darwin' if sys.platform == 'darwin' else 'linux')
    if result.get('platform') != expected_platform:
        problems.append(f"platform is {result.get('platform')!r}, running on {expected_platform!r}")
    for field in ('toolrush_version', 'python', 'python_executable'):
        if not result.get(field):
            problems.append(f'missing report field: {field}')

    # 2. Integrity: every hash-pinned helper must verify on every platform.
    #    A non-empty payload dict that stops early is the CRLF/partial-read signal.
    helpers = json.loads((PLUGIN / 'payload.json').read_text(encoding='utf-8'))['helpers']
    reported = result.get('payload') or {}
    missing = sorted(set(helpers) - set(reported))
    unverified = sorted(k for k, v in reported.items() if v != 'verified')
    if missing:
        problems.append(f'payload entries never verified (aborted early?): {missing}')
    if unverified:
        problems.append(f'payload entries not verified: {unverified}')

    # 3. Any unexpected exception is a failure regardless of Hermes presence.
    if 'error' in result:
        problems.append(f"unexpected doctor error: {result['error']!r}")

    # 4. Fail-closed contract: exit code must follow ok, ok must follow Hermes.
    hermes_root = result.get('hermes_root')
    ok = result.get('ok')
    if hermes_root:
        if ok is True:
            if args.exit_code != EXIT_OK:
                problems.append(f'ok=true but exit code is {args.exit_code}, expected 0')
            not_ready = [k for k, v in (result.get('lanes') or {}).items()
                         if v.get('status') not in OK_LANE_STATES]
            if not_ready:
                problems.append(f'ok=true but lanes are not ready: {not_ready}')
        elif ok is False:
            if args.exit_code != EXIT_FAIL_CLOSED:
                problems.append(f'ok=false but exit code is {args.exit_code}, expected 2')
        else:
            problems.append(f'ok must be a boolean, got {ok!r}')
    else:
        if result.get('hermes_status') != 'missing':
            problems.append(f"hermes_root is null but hermes_status is {result.get('hermes_status')!r}")
        if ok is not False:
            problems.append(f'no Hermes install must set ok=false (fail closed), got {ok!r}')
        if args.exit_code != EXIT_FAIL_CLOSED:
            problems.append(f'no Hermes install must exit {EXIT_FAIL_CLOSED}, got {args.exit_code}')

    # 5. Smoke mode must actually boot the plugin, and must not claim a lane it
    #    cannot exercise without Hermes.
    if args.smoke:
        boot = result.get('boot') or {}
        if boot.get('status') not in OK_BOOT_STATES:
            problems.append(f"smoke boot status is {boot.get('status')!r} ({boot.get('reason')})")
        warm = (result.get('smoke_execution') or {}).get('warm_shell')
        expected_warm = 'passed' if hermes_root else 'failed'
        if warm != expected_warm:
            problems.append(f'smoke_execution.warm_shell is {warm!r}, expected {expected_warm!r}')

    if problems:
        print(json.dumps(result, indent=2, ensure_ascii=False)[:4000])
        for p in problems:
            print(f'  FAIL: {p}')
        print(f'doctor contract: FAILED ({len(problems)} problem(s)), exit_code={args.exit_code}')
        return 1

    mode = 'hermes present' if hermes_root else 'clean environment (fail-closed)'
    print(f'doctor contract: OK -- {mode}, payload {len(reported)}/{len(helpers)} verified, '
          f'ok={ok}, exit_code={args.exit_code}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
