"""Switch the demo dataset in a running server.

Each generated demo has its own data folder (``.folio-data/<demo>``), so the store, the
reports, and the source of a demo stay as they are when another demo is selected. A switch
prepares the new demo completely (data, model, source) before it selects it, so a request
sees either the old demo or the new one. The switch applies to the whole server.
"""
import os
import threading
from pathlib import Path

from living_reports.config import Settings, get_settings
from living_reports.demos import get_demo

DATASETS = ("retail", "fraud", "hr")
_lock = threading.Lock()


class DemoSwitchError(ValueError):
    """The demo cannot be switched to this dataset in this deployment."""


def _own_folder(settings: Settings) -> bool:
    # A fixed data folder (LR_DATA_DIR) would put two demos in one store.
    return settings.data_dir == Path(".folio-data") / settings.demo


def _switchable(settings: Settings) -> bool:
    return settings.demo_mode and settings.demo in DATASETS and _own_folder(settings)


def datasets() -> list[dict[str, str]]:
    """The datasets a person can select, or none when the deployment cannot switch."""
    if not _switchable(get_settings()):
        return []
    return [{"name": name, "title": get_demo(name).title} for name in DATASETS]


def switch_demo(name: str) -> None:
    """Prepare the demo ``name`` and then select it for every later request."""
    from living_reports.sources.build import build_demo_source, demo_moment
    from living_reports.store import get_store

    if name not in DATASETS:
        raise DemoSwitchError(f"There is no dataset {name!r}.")
    if not _switchable(get_settings()):
        raise DemoSwitchError("This deployment uses one fixed dataset.")
    with _lock:
        target = Settings(demo=name)
        if not _own_folder(target):
            raise DemoSwitchError("This deployment uses one fixed dataset.")
        store = get_store(target)
        get_demo(name).seed(store.graph)
        build_demo_source(demo_moment(store) or 1, target)
        os.environ["LR_DEMO"] = name
