"""Canonical job shape shared by every job source.

Every adapter must return a pandas DataFrame using (a subset of) these
columns. Missing columns are filled in by :func:`normalize_jobs`, so a new
source only has to produce the fields it actually knows about.
"""

from __future__ import annotations

import pandas as pd

CANONICAL_COLUMNS = [
    "title",
    "company",
    "location",
    "job_type",
    "description",
    "job_url",
    "job_url_direct",
    "site",
    "date_posted",
    "min_amount",
    "max_amount",
    "currency",
    "interval",
    "is_remote",
    "company_url",
]


def normalize_jobs(jobs: pd.DataFrame, *, site: str) -> pd.DataFrame:
    """Return ``jobs`` with every canonical column present."""
    normalized = jobs.copy()
    for column in CANONICAL_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    if site:
        normalized["site"] = site
    return normalized[CANONICAL_COLUMNS + [c for c in normalized.columns if c not in CANONICAL_COLUMNS]]
