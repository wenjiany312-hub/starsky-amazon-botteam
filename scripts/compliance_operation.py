"""Native, operation-local read snapshot; never serialized or cached across actions."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
from pathlib import Path

_ACTIVE = ContextVar('s2_compliance_operation', default=None)


class ChangedDuringOperation(ValueError):
    pass


class _Snapshot:
    def __init__(self):
        self.files = {}
        self.derived = {}

    def read_bytes(self, path):
        path = Path(path).resolve()
        if path not in self.files:
            self.files[path] = path.read_bytes() if path.is_file() else None
        return self.files[path]

    def verify_current(self):
        for path, previous in self.files.items():
            current = path.read_bytes() if path.is_file() else None
            if current != previous:
                raise ChangedDuringOperation('compliance_inputs_changed_during_operation:' + str(path))


def current():
    return _ACTIVE.get()


def read_bytes(path):
    active = current()
    if active is not None:
        return active.read_bytes(path)
    path = Path(path)
    return path.read_bytes() if path.is_file() else None


def cached(key, factory):
    active = current()
    if active is None:
        return factory()
    if key not in active.derived:
        active.derived[key] = factory()
    return active.derived[key]


@contextmanager
def scope():
    active = current()
    if active is not None:
        yield active
        return
    active = _Snapshot()
    token = _ACTIVE.set(active)
    try:
        yield active
        active.verify_current()
    finally:
        _ACTIVE.reset(token)


def boundary(function):
    @wraps(function)
    def run(*args, **kwargs):
        with scope():
            return function(*args, **kwargs)
    return run
