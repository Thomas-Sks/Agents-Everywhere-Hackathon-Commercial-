"""JSON file persistence — backing store for the local adapters.

Atomic write (temporary file then `os.replace`) and a process-level lock: an interruption at
the wrong moment must not leave truncated state, and two concurrent FastAPI requests must not
overwrite each other.

Good enough for a single-instance deployment. Beyond that, replace it with a Postgres adapter —
which is precisely what the ports architecture makes possible without touching the domain.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class JsonDocument:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> dict[str, Any]:
        with self._lock:
            if not self._path.exists():
                return {}
            with self._path.open("r", encoding="utf-8") as handle:
                return json.load(handle)

    def write(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # delete=False is essential: the file must survive being closed so it can be
            # atomically renamed onto the target. Cleanup is handled by the `except` block.
            handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
                "w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            )
            try:
                with handle:
                    json.dump(data, handle, indent=2, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, self._path)
            except BaseException:
                Path(handle.name).unlink(missing_ok=True)
                raise

    @contextmanager
    def update(self) -> Iterator[dict[str, Any]]:
        """Read-modify-write under lock."""
        with self._lock:
            data = self.read()
            yield data
            self.write(data)
