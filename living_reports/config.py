"""Settings. Every value can come from an ``LR_`` environment variable."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceConfig(BaseModel):
    """One configured source. ``LR_SOURCES`` holds a JSON object of id -> config."""

    kind: Literal["duckdb", "postgres"]
    path: str | None = None
    dsn: str | None = None
    files: dict[str, str] = {}

    @model_validator(mode="after")
    def _location(self) -> "SourceConfig":
        if self.kind == "duckdb" and not (self.path or self.files):
            raise ValueError("A duckdb source needs a path or files.")
        if self.kind == "postgres" and not self.dsn:
            raise ValueError("A postgres source needs a dsn.")
        return self

    def __repr__(self) -> str:  # a DSN can hold a password
        return f"SourceConfig(kind={self.kind!r})"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LR_", extra="ignore")

    # The demo: "retail", "fraud", or "hr" (generated data), or "orders" (the unit tests'
    # model, with its data in source_path).
    demo: Literal["retail", "fraud", "hr", "orders"] = "retail"
    # Default: .folio-data/<demo>, so the demos never share reports, caches, or graphs.
    data_dir: Path = Path(".folio-data")
    # The Parquet file of the orders demo.
    source_path: Path | None = None
    sources: dict[str, SourceConfig] = {}
    # The Postgres DSN for the demo loader script and the live Postgres tests.
    pg_dsn: str | None = None
    port: int = 8100
    small_model: str | None = None
    frontier_model: str | None = None
    checker_model: str | None = None
    # A decision model (``/v1/systemone``) for choices, and the judge of the narrative.
    # Each step is off when its model is not set. The judge defaults to the decision model.
    decide_model: str | None = None
    judge_model: str | None = None
    # The server with ``/v1/systemone``. Default: the Ollama server.
    decide_base_url: str | None = None
    # Probabilities from a decision model are not calibrated; tune these with the eval.
    # "decision": the decision model routes a plain question, and the language model routes
    # the rest. "model": the language model routes every question.
    route_mode: Literal["decision", "model"] = "decision"
    decide_confident: float = 0.8
    decide_unsure: float = 0.75
    decide_answerable_floor: float = 0.3
    judge_floor: float = 0.5
    # Writer attempts before the code-only text: each attempt runs the code checks and the rubric.
    narrate_attempts: int = 3
    # Order the follow-up chips with the decision model. Off: the insight eval found the 4B
    # ranking below the vocabulary order.
    rank_follow_ups: bool = False
    # The decision model that ranks the chips. Default: LR_DECIDE_MODEL.
    rank_model: str | None = None
    demo_mode: bool = True
    public_url: str = "http://localhost:3100"
    export_template: Path = Path("living_reports/web/dist-export/export.html")

    @model_validator(mode="after")
    def _demo_data_dir(self) -> "Settings":
        if "data_dir" not in self.model_fields_set and self.demo != "orders":
            self.data_dir = Path(".folio-data") / self.demo
        return self


def get_settings() -> Settings:
    # Read on each call: tests and the demo change the environment between runs.
    return Settings()
