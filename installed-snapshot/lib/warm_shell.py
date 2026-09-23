"""macOS streaming warm-shell substrate for ToolRush.

Maintains one reusable broker per LocalEnvironment. Each command runs in an isolated
subshell; the caller's existing wrapper remains authoritative for state, exit status,
cwd, and secret exclusions. POSIX process groups ensure clean process tree termination.
"""

import contextlib
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import uuid
from typing import Optional, Tuple


class MacOSWarmShell:
    def __init__(self, local, owner):
        from tools.environments.local import _find_bash, _make_run_env

        sanitized = _make_run_env(owner.env)
        keep = {
            "PATH",
            "HOME",
            "USER",
            "SHELL",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "TERM",
        }
        broker_env = {k: v for k, v in sanitized.items() if k in keep}
        cwd = owner.cwd or os.path.expanduser("~")

        self.proc = subprocess.Popen(
            [_find_bash(), "--noprofile", "--norc", "-s"],
            cwd=cwd,
            env=broker_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            preexec_fn=os.setsid,  # Create dedicated process group on macOS
        )
        self.lock = threading.Lock()
        self.dead = False

    def close(self):
        self.dead = True
        if self.proc.poll() is None:
            try:
                pgid = os.getpgid(self.proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(0.05)
                if self.proc.poll() is None:
                    os.killpg(pgid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                with contextlib.suppress(Exception):
                    self.proc.kill()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        for pipe in (self.proc.stdin, self.proc.stdout):
            if pipe:
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass


def build_frame(owner, local, command: str, timeout: Optional[int] = None) -> Tuple[bytes, bytes, bytes, Optional[Tuple[str, str, str]]]:
    from tools.environments.local import _make_run_env

    uid = uuid.uuid4().hex
    env = _make_run_env(owner.env)
    lines = command.split("\n")
    prefix = "__hermes_snap_tmp=$(mktemp "
    allocations = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    commit = None

    if len(allocations) == 1:
        i = allocations[0]
        line = lines[i]
        boundary = line.find(") && ")
        move = '&& mv -f "$__hermes_snap_tmp" ' + owner._quote_shell_path(owner._snapshot_path) + ";"
        if boundary > len(prefix) and move in line:
            temporary = owner._snapshot_path + ".tmp." + uid
            ready = temporary + ".ready"
            unique = owner._quote_shell_path(temporary)
            lines[i] = f"__hermes_snap_tmp={unique}; " + line[boundary + 5:]
            lines[i] = lines[i].replace(move, "&& : > " + owner._quote_shell_path(ready) + ";", 1)
            commit = (temporary, owner._snapshot_path, ready)

    exports = []
    for key, value in env.items():
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and isinstance(value, str):
            exports.append(f"export {key}={shlex.quote(value)}")

    begin = ("__TRB_" + uid + "__\n").encode("utf-8")
    end = ("\n__TRE_" + uid + ":").encode("utf-8")
    body = "\n".join(lines)
    frame = (
        f"printf '%s\\n' '__TRB_{uid}__'\n(\n"
        + "\n".join(exports)
        + "\n"
        + body
        + f"\n) </dev/null\n__tr_rc=$?\nprintf '\\n__TRE_{uid}:%s\\n' \"$__tr_rc\"\n"
    )
    return frame.encode("utf-8"), begin, end, commit


class WarmHandle:
    """ProcessHandle adapter streaming over OS pipes with bounded backpressure."""

    def __init__(self, shell: MacOSWarmShell, frame: bytes, begin: bytes, end: bytes, commit=None):
        self.shell = shell
        self.commit = commit
        if commit:
            fd = os.open(commit[0], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self._done = threading.Event()
        self._returncode = None
        read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        threading.Thread(
            target=self._run,
            args=(frame, begin, end),
            daemon=True,
            name="toolrush-warm-frame",
        ).start()

    @property
    def pid(self) -> int:
        return self.shell.proc.pid

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    def poll(self) -> Optional[int]:
        return self._returncode if self._done.is_set() else None

    def wait(self, timeout: Optional[float] = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("toolrush-frame", timeout)
        return self._returncode

    def kill(self):
        self.shell.close()

    def _emit(self, data):
        view = memoryview(data)
        while view:
            written = os.write(self._write_fd, view)
            view = view[written:]

    def _run(self, frame: bytes, begin: bytes, end: bytes):
        proc = self.shell.proc
        buf = bytearray()
        started = False
        try:
            view = memoryview(frame)
            while view:
                n = proc.stdin.write(view)
                view = view[n:]

            while True:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    self._returncode = 1
                    self.shell.dead = True
                    break
                buf.extend(chunk)
                if not started:
                    pos = buf.find(begin)
                    if pos < 0:
                        if len(buf) > len(begin):
                            del buf[:-len(begin)]
                        continue
                    del buf[: pos + len(begin)]
                    started = True
                pos = buf.find(end)
                if pos >= 0:
                    eol = buf.find(b"\n", pos + len(end))
                    if eol >= 0:
                        self._emit(buf[:pos])
                        try:
                            self._returncode = int(buf[pos + len(end) : eol].strip())
                        except ValueError:
                            self._returncode = 1
                            self.shell.dead = True
                        if self.commit:
                            temporary, target, ready = self.commit
                            if os.path.isfile(ready):
                                try:
                                    os.replace(temporary, target)
                                except OSError:
                                    self._returncode = 1
                                    self.shell.dead = True
                        break
                if pos >= 0:
                    if pos:
                        self._emit(buf[:pos])
                        del buf[:pos]
                else:
                    keep = min(len(end) - 1, len(buf))
                    while keep and not buf.endswith(end[:keep]):
                        keep -= 1
                    amount = len(buf) - keep
                    if amount:
                        self._emit(buf[:amount])
                        del buf[:amount]
        except Exception:
            self._returncode = 1
            self.shell.dead = True
        finally:
            if self.commit:
                for path in (self.commit[0], self.commit[2]):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            if self._returncode is None:
                self._returncode = 1
            try:
                os.close(self._write_fd)
            except OSError:
                pass
            self._done.set()
            self.shell.lock.release()
