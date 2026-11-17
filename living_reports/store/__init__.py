"""The process-wide store. ``get_store()`` gives one instance per database file."""

import threading
from pathlib import Path

from living_reports.config import Settings, get_settings
from living_reports.store.sqlite import Store

__all__ = ["Store", "get_store", "reset_store"]

_stores: dict[Path, Store] = {}
_lock = threading.Lock()


def get_store(settings: Settings | None = None) -> Store:
    """The one store for ``<data_dir>/app.sqlite``, opened on first use."""
    data_dir = (settings or get_settings()).data_dir.expanduser().resolve()
    path = data_dir / "app.sqlite"
    with _lock:
        store = _stores.get(path)
        if store is None:
            data_dir.mkdir(parents=True, exist_ok=True)
            store = _stores[path] = Store(path)
        return store


def reset_store() -> None:
    """Close and forget every store. Tests only."""
    with _lock:
        for store in _stores.values():
            store.close()
        _stores.clear()
