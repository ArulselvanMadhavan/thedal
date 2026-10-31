"""Pluggable job sources.

Standard way to add a job source
---------------------------------
1. **New kind of board?** Write an adapter module in this package exposing
   ``fetch_<kind>_jobs(config: dict, *, site: str) -> pd.DataFrame``
   returning canonical columns (see :mod:`job_sources.base`), and register
   it in :data:`ADAPTERS` below under its ``type`` name.
   **Same kind, new board?** Skip the code — go to step 2.
2. Add a ``[[sources]]`` entry to ``sources.toml`` with a unique ``name``,
   its ``type``, and that type's config (e.g. ``url`` for Notion).
3. Reference the source ``name`` in a profile's ``sources`` list
   (``"sources": ["jobspy", "notion-transformer-talent"]``). Profiles that
   omit ``sources`` only search JobSpy boards, as before.

The built-in ``jobspy`` type is special: it is searched with the profile's
role terms/locations by ``search_jobs.py`` itself, not through an adapter.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .base import CANONICAL_COLUMNS, normalize_jobs
from .notion import fetch_notion_jobs

__all__ = [
    "ADAPTERS",
    "CANONICAL_COLUMNS",
    "SourceSpec",
    "fetch_source",
    "load_source_specs",
    "normalize_jobs",
]

Adapter = Callable[..., pd.DataFrame]

ADAPTERS: dict[str, Adapter] = {
    "notion": fetch_notion_jobs,
}

JOBSPY_TYPE = "jobspy"
DEFAULT_SOURCES_FILE = Path(__file__).resolve().parent.parent / "sources.toml"


@dataclass
class SourceSpec:
    name: str
    type: str
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    @property
    def is_jobspy(self) -> bool:
        return self.type == JOBSPY_TYPE


def load_source_specs(path: Path | str | None = None) -> list[SourceSpec]:
    """Load ``[[sources]]`` entries from a TOML file (default: repo ``sources.toml``)."""
    sources_path = Path(path) if path else DEFAULT_SOURCES_FILE
    if not sources_path.exists():
        return [SourceSpec(name=JOBSPY_TYPE, type=JOBSPY_TYPE)]
    with sources_path.open("rb") as handle:
        data = tomllib.load(handle)
    specs: list[SourceSpec] = []
    for entry in data.get("sources", []):
        name = str(entry.get("name", "")).strip()
        source_type = str(entry.get("type", "")).strip()
        if not name or not source_type:
            raise ValueError(f"Each [[sources]] entry needs name and type: {entry!r}")
        config = {k: v for k, v in entry.items() if k not in {"name", "type", "enabled"}}
        specs.append(
            SourceSpec(name=name, type=source_type, config=config, enabled=bool(entry.get("enabled", True)))
        )
    return specs


def resolve_specs(specs: list[SourceSpec], wanted: list[str]) -> list[SourceSpec]:
    """Pick the enabled specs named in ``wanted`` (order preserved)."""
    by_name = {spec.name: spec for spec in specs if spec.enabled}
    resolved: list[SourceSpec] = []
    for name in wanted:
        spec = by_name.get(name)
        if spec is None:
            print(f"Warning: source {name!r} not found/enabled in sources.toml — skipping")
            continue
        resolved.append(spec)
    return resolved


def fetch_source(spec: SourceSpec) -> pd.DataFrame:
    """Fetch one non-JobSpy source through its registered adapter."""
    adapter = ADAPTERS.get(spec.type)
    if adapter is None:
        raise ValueError(
            f"No adapter registered for source type {spec.type!r} "
            f"(source {spec.name!r}); register one in job_sources.ADAPTERS"
        )
    return adapter(spec.config, site=spec.name)
