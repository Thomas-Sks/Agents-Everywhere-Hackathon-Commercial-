"""Persistance JSON sur fichier — support des adapters locaux.

Écriture atomique (fichier temporaire puis `os.replace`) et verrou process : une interruption
au mauvais moment ne doit pas laisser un état tronqué, et deux requêtes FastAPI concurrentes ne
doivent pas s'écraser mutuellement.

Suffisant pour un déploiement mono-instance. Au-delà, remplacer par un adapter Postgres —
c'est précisément ce que l'architecture en ports rend possible sans toucher au domaine.
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
            # delete=False est indispensable : le fichier doit survivre à la fermeture pour
            # être renommé atomiquement sur la cible. Le nettoyage est assuré par le `except`.
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
        """Lecture-modification-écriture sous verrou."""
        with self._lock:
            data = self.read()
            yield data
            self.write(data)
