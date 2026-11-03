"""One engine process per API key: a pid lock (standard library only). Used by services/engine_server.py and by the
web app's bridge (app/bridge/bridge.py), which share the lock of a key: starting one while the other runs refuses."""
from __future__ import annotations

import contextlib
import hashlib
import os


class InstanceLock:
    """A pid file. A second start finds a live pid and refuses; a dead pid (crash) is taken over.
    The engine service and the web app's bridge share the lock file of a key, so they exclude each other."""

    def __init__(self, path: str):
        self.path = path

    @staticmethod
    def _alive(pid: int) -> bool:
        if pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        with contextlib.suppress(OSError):                 # a recycled pid belongs to some other program
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                data = fh.read()
                return b"engine_server" in data or b"bridge" in data
        return True

    def holder(self) -> int | None:
        try:
            pid = int(open(self.path).read().split()[0])
        except (OSError, ValueError, IndexError):
            return None
        return pid if self._alive(pid) else None

    def acquire(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pid = self.holder()
                if pid is not None:
                    raise SystemExit(f"another engine service (pid {pid}) already runs for this key; "
                                     f"one process per key ({self.path})")
                with contextlib.suppress(FileNotFoundError):
                    os.remove(self.path)                    # stale: its process is gone
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(f"{os.getpid()}\n")
            return
        raise SystemExit(f"could not take the instance lock {self.path}")

    def release(self) -> None:
        if self.holder() == os.getpid():
            with contextlib.suppress(FileNotFoundError):
                os.remove(self.path)


def lock_path(api_key: str, directory: str | None = None) -> str:
    """One lock per key. The name carries a short domain-separated digest, never the key."""
    tag = hashlib.sha256(b"ednova-engine-service-lock:" + api_key.encode()).hexdigest()[:10]
    return os.path.join(directory or os.environ.get("ENGINE_SERVICE_LOCK_DIR", "runtime"), f"engine-{tag}.lock")


