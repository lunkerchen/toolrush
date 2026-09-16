"""Conservative terminal read admission for parallel scheduling.

Not command approval: unknown commands still execute via the normal sequential
approval path. Rejecting acceleration must never mean rejecting legitimate work.

Admission decides read-only parallel eligibility ONLY.
Any failure to prove that a command is 100% free of side-effects must fail closed
(return False), forcing standard sequential execution with normal user authorization.
Do not confuse flag rejection with preventing environment-based execution.
"""
import os
import re
import shlex

_SIMPLE = frozenset({
    'cat','grep','head','tail','wc','stat','file','pwd','echo',
    'which','type','whereis','whoami','uname','printenv','id','groups',
    'df','du','free','uptime','ps','tasklist','nproc','lscpu','diff','cmp',
    'md5sum','sha1sum','sha256sum','sha512sum','cksum','cut','column','nl',
    'rev','strings','tr','readlink','realpath','basename','dirname','seq',
    'tac','od','expr','test','true','false',
})


def _stage(stage: str) -> bool:
    try:
        tokens = shlex.split(stage, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    first, args = tokens[0], tokens[1:]

    # Shell function / alias declarations or definitions are stateful barriers
    if first in {'function', 'alias', 'unalias'}:
        return False

    # Assignments / env wrappers can change later shared shell state or hide a
    # command. Even assignment-only invocations must be barriers.
    if first not in _SIMPLE and first not in {
        'rg','sed','python','python3','py','node','npm','pip','pip3','uv',
        'date','hostname','printf','sort','uniq','jq',
    }:
        # Note:
        # 1. curl is intentionally NOT admitted to parallel execution to prevent
        #    side effects from ~/.curlrc, redirects, credentials, or remote endpoints.
        # 2. git is NOT admitted to parallel execution without verified environment/config
        #    scrubbing, because diff.external, textconv, GIT_EXTERNAL_DIFF, GIT_CONFIG_*,
        #    and repo-level config hooks can trigger arbitrary command execution.
        #    Sequential execution via standard backend handles git safely.
        return False

    if first in {'python','python3','py','node','npm','pip','pip3','uv'}:
        return len(args) == 1 and args[0] in {'--version','-V','--help'}

    if first == 'date':
        return all(a.startswith('+') or a in {'-u','--utc','--iso-8601','--version'} for a in args)

    if first == 'hostname':
        # Ensure HOSTNAME_BIN or hostile external commands are not executed
        return not args or args in (['--version'],['-f'],['-s'],['-I'])

    if first == 'printf':
        return bool(args) and args[0] != '-v'

    if first == 'sed':
        # Only a numeric print-address expression, never arbitrary sed code,
        # w/e commands, script files, or substitution flags.
        return (len(args) >= 2 and args[0] == '-n'
                and re.fullmatch(r'\d+(?:,\d+)?p', args[1]) is not None
                and all(not a.startswith('-') for a in args[2:]))

    if first == 'sort':
        return not any(a.startswith(('-o','--output','--compress-program','-T','--temporary-directory')) for a in args)

    if first == 'uniq':
        # A second operand is an output filename. Ambiguous arg-taking flags
        # stay sequential; ordinary -c/-d/-u plus one input remain concurrent.
        return (all(a in {'-c','-d','-u','-i'} for a in args if a.startswith('-'))
                and len([a for a in args if not a.startswith('-')]) <= 1)

    if first == 'jq':
        # jq --run-tests has file effects; module loading can execute custom
        # behavior. Ordinary filters have no write primitive.
        return not any(a.startswith(('--run-tests','-L','--library-path')) for a in args)

    if first == 'rg':
        # Check hostile environment: RIPGREP_CONFIG_PATH could point to --pre or --hostname-bin
        if 'RIPGREP_CONFIG_PATH' in os.environ:
            return False
        # Disallow --pre external preprocessor, --hostname-bin, and archive decompression (-z/--search-zip)
        return not any(a.startswith(('--pre','--hostname-bin','-z','--search-zip')) for a in args)

    return True


def readonly(command: str) -> bool:
    if not isinstance(command, str) or not command.strip():
        return False

    # Check for shell function / alias syntax: e.g. foo() { ... }
    if re.search(r'(?:^|\s)\w+\s*\(\s*\)\s*\{', command):
        return False

    # Shell syntax with hidden processes, writable redirections or parameter
    # assignment is not eligible. Plain $NAME/path args remain supported.
    if any(s in command for s in ('`','$(', '>','<','${!','${=')):
        return False
    if re.search(r'\$\{[^}]*[^\w}][^}]*\}', command):
        return False
    if re.search(r'(?<!&)&(?!&)', command):
        return False

    # Check for environment variable assignments preceding commands or standalone (FOO=bar)
    # Tokenizing segments
    segments = re.split(r'\s*(?:;|&&|\|\||\|)\s*|\n', command)
    for s in segments:
        s_clean = s.strip()
        if not s_clean:
            continue
        # If segment contains assignment before command e.g. "FOO=bar cmd" or "FOO=bar"
        first_token = s_clean.split()[0]
        if '=' in first_token and not first_token.startswith('-'):
            return False

    return bool(segments) and all(_stage(s.strip()) for s in segments if s.strip())
