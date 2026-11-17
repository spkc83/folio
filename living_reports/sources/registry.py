"""The process-wide sources, built from settings: one instance per source id."""
import threading
from pathlib import Path

from living_reports.config import Settings, SourceConfig, get_settings
from living_reports.semantic.demo import DEMO_SOURCE
from living_reports.sources.base import Source, SourceError
from living_reports.sources.build import build_duckdb_file, demo_available, demo_source_path
from living_reports.sources.duckdb import DuckDBSource
from living_reports.store import Store

__all__ = ["SourceConfig", "active_source", "reset_sources", "source_for", "sources"]

_lock = threading.Lock()
# (id, location) -> source. The location is in the key so that a changed setting (a new
# data directory in a test) never returns a source for the old location.
_instances: dict[tuple[str, str], Source] = {}


def _configs(settings: Settings) -> dict[str, SourceConfig | None]:
    """Configured ids in order: the demo source first (``None`` config), then LR_SOURCES."""
    configs: dict[str, SourceConfig | None] = {}
    if demo_available(settings):
        configs[DEMO_SOURCE] = None
    for source_id, config in settings.sources.items():
        configs.setdefault(source_id, config)
    return configs


def sources() -> list[str]:
    return list(_configs(get_settings()))


def _reader(path: str) -> str:
    quoted = "'" + path.replace("'", "''") + "'"
    if path.lower().endswith((".csv", ".tsv", ".csv.gz")):
        return f"SELECT * FROM read_csv_auto({quoted})"
    return f"SELECT * FROM read_parquet({quoted})"


def _files_path(source_id: str, config: SourceConfig, settings: Settings) -> Path:
    """Copy the configured files into one DuckDB file (plan ruling 1); rebuild on change."""
    target = settings.data_dir.expanduser().resolve() / "sources" / f"{source_id}.duckdb"
    newest = max(Path(p).expanduser().stat().st_mtime for p in config.files.values())
    if not target.exists() or target.stat().st_mtime < newest:
        tables = {name: _reader(str(Path(p).expanduser())) for name, p in config.files.items()}
        build_duckdb_file(target, tables)
    return target


def _location(source_id: str, config: SourceConfig | None, settings: Settings) -> str:
    if config is None:
        return str(demo_source_path(settings))
    if config.kind == "postgres":
        return f"postgres:{config.dsn}"
    return str(config.path) if config.path else f"files:{sorted(config.files.items())}"


def _make(source_id: str, config: SourceConfig | None, settings: Settings) -> Source:
    if config is None:
        return DuckDBSource(DEMO_SOURCE, demo_source_path(settings))
    if config.kind == "postgres":
        from living_reports.sources.postgres import PostgresSource

        return PostgresSource(source_id, config.dsn or "")
    if config.path:
        return DuckDBSource(source_id, Path(config.path).expanduser())
    try:
        return DuckDBSource(source_id, _files_path(source_id, config, settings))
    except OSError as error:
        raise SourceError(f"{source_id}: a configured file is missing") from error


def source_for(source_id: str) -> Source:
    settings = get_settings()
    configs = _configs(settings)
    if source_id not in configs:
        raise SourceError(f"No source is configured for {source_id}")
    config = configs[source_id]
    key = (source_id, _location(source_id, config, settings))
    with _lock:
        source = _instances.get(key)
        if source is None:
            source = _instances[key] = _make(source_id, config, settings)
        return source


def reset_sources() -> None:
    """Close and forget every source. Tests only."""
    with _lock:
        for source in _instances.values():
            close = getattr(source, "close", None)
            if close:
                close()
        _instances.clear()


def active_source(store: Store) -> str:
    """The one source of the current model."""
    source = store.graph.model().source
    if source is None:
        raise SourceError("The model names no single source; give a source id")
    return source
