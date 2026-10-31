"""Adapter for a public Notion database (collection) of jobs.

Works with any Notion page shared as "public" that is a collection view
(table/board/...). No API token is needed: the adapter talks to the same
unauthenticated ``/api/v3`` endpoints the Notion web client uses
(``loadPageChunk`` to discover the collection + schema, then
``queryCollection`` to read the rows).

Adding another Notion board never needs new code — just add a
``[[sources]]`` entry of ``type = "notion"`` in ``sources.toml`` and
reference its ``name`` from a profile's ``sources`` list.

Column mapping (Notion property name -> canonical column); property names
are matched case-insensitively, so boards with slightly different headers
still work:

    Job Title / Title / Name -> title        Locations / Location -> location
    Description              -> description  Tech Stack           -> (+description)
    Industry / Role Type / Funding / Team Size / Equity / Visa / YOE
                             -> (+description, so scoring & startup
                                  filtering can see them)
    Salary (Base) / Salary   -> min_amount / max_amount (yearly USD)
    Workplace                -> job_type (+ is_remote when it says Remote)
    (row page id)            -> job_url (the public Notion page for the row)
    (row created_time)       -> date_posted

There is no company column on these boards, so ``company`` is left blank.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests

from .base import normalize_jobs

NOTION_API = "https://www.notion.so/api/v3"
_HEADERS = {"User-Agent": "thedal-job-search/1.0", "Content-Type": "application/json"}
# Upper bound on rows pulled from one board per run.
MAX_ROWS = 1000


def _dashed_uuid(value: str) -> str:
    compact = value.replace("-", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", compact):
        raise ValueError(f"Not a Notion id: {value!r}")
    compact = compact.lower()
    return f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:]}"


def parse_notion_url(url: str) -> tuple[str, str | None]:
    """Extract ``(page_id, view_id|None)`` (dashed) from a public Notion URL."""
    parsed = urlparse(url)
    if "notion." not in parsed.netloc:
        raise ValueError(f"Not a Notion URL: {url}")
    segment = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    # Page slugs look like ``My-Page-2b55dfe3e657804bafa3d2ae46dfd587`` or a bare id.
    match = re.search(r"([0-9a-fA-F]{32})$", segment.replace("-", ""))
    if not match:
        # Fall back to the last 32 hex chars of the raw segment.
        hex_only = re.sub(r"[^0-9a-fA-F]", "", segment)
        if len(hex_only) < 32:
            raise ValueError(f"Could not find a Notion page id in URL: {url}")
        match = re.search(r"([0-9a-fA-F]{32})$", hex_only)
    page_id = _dashed_uuid(match.group(1))
    view_values = parse_qs(parsed.query).get("v", [])
    view_id = _dashed_uuid(view_values[0]) if view_values else None
    return page_id, view_id


def _flatten_text(value: Any) -> str:
    """Flatten Notion rich-text / property values to a plain string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [_flatten_text(item) for item in value]
        return "".join(parts)
    if isinstance(value, dict):
        return _flatten_text(value.get("value", ""))
    return str(value)


def _property_text(properties: dict[str, Any], schema_by_name: dict[str, str], *names: str) -> str:
    for name in names:
        prop_id = schema_by_name.get(name.lower())
        if prop_id and prop_id in properties:
            text = _flatten_text(properties[prop_id]).strip()
            if text:
                return text
    return ""


def parse_salary_range(text: str) -> tuple[float | None, float | None]:
    """Parse ``$180K - $220K`` / ``$150,000`` style ranges to yearly amounts."""
    if not text:
        return None, None
    amounts: list[float] = []
    for number, suffix in re.findall(r"\$?\s*([\d,]+(?:\.\d+)?)\s*([KkMm])?", text):
        amount = float(number.replace(",", ""))
        if suffix.lower() == "k":
            amount *= 1_000
        elif suffix.lower() == "m":
            amount *= 1_000_000
        elif amount < 10_000:  # bare "180" in a salary column almost always means $180K
            amount *= 1_000
        amounts.append(amount)
    if not amounts:
        return None, None
    return min(amounts), max(amounts)


def jobs_from_record_map(
    record_map: dict[str, Any],
    schema: dict[str, Any],
    *,
    site: str,
) -> pd.DataFrame:
    """Turn a Notion ``queryCollection`` recordMap into canonical jobs."""
    schema_by_name = {
        str(spec.get("name", "")).lower(): prop_id
        for prop_id, spec in schema.items()
        if isinstance(spec, dict)
    }
    title_prop = next(
        (prop_id for prop_id, spec in schema.items() if isinstance(spec, dict) and spec.get("type") == "title"),
        "title",
    )

    rows: list[dict[str, Any]] = []
    blocks = record_map.get("block", {})
    for block in blocks.values():
        value = (block or {}).get("value", {}).get("value", {})
        if value.get("type") != "page" or not value.get("properties"):
            continue
        properties: dict[str, Any] = value["properties"]
        title = _flatten_text(properties.get(title_prop or "title", "")).strip()
        if not title:
            continue

        location = _property_text(properties, schema_by_name, "Locations", "Location")
        workplace = _property_text(properties, schema_by_name, "Workplace", "Job Type")
        salary_text = _property_text(properties, schema_by_name, "Salary (Base)", "Salary")
        min_amount, max_amount = parse_salary_range(salary_text)

        description_bits = []
        for label in (
            "Description", "Tech Stack", "Industry", "Role Type", "Funding",
            "Team Size", "Equity", "Visa", "YOE",
        ):
            text = _property_text(properties, schema_by_name, label)
            if text:
                description_bits.append(text if label == "Description" else f"{label}: {text}")
        if salary_text:
            description_bits.append(f"Salary: {salary_text}")

        created_ms = value.get("created_time")
        date_posted = (
            datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).date().isoformat()
            if isinstance(created_ms, (int, float)) and created_ms
            else ""
        )
        block_id = str(value.get("id", ""))
        rows.append(
            {
                "title": title,
                "company": "",
                "location": location,
                "job_type": workplace,
                "description": "\n".join(description_bits),
                "job_url": f"https://www.notion.so/{block_id.replace('-', '')}" if block_id else "",
                "site": site,
                "date_posted": date_posted,
                "min_amount": min_amount,
                "max_amount": max_amount,
                "currency": "USD" if min_amount is not None else "",
                "interval": "yearly" if min_amount is not None else "",
                "is_remote": "remote" in f"{workplace} {location}".lower(),
            }
        )
    return normalize_jobs(pd.DataFrame(rows), site=site)


def fetch_notion_jobs(config: dict[str, Any], *, site: str) -> pd.DataFrame:
    """Fetch all rows from the public Notion collection in ``config['url']``."""
    url = str(config.get("url", ""))
    if not url:
        raise ValueError("notion source requires a 'url'")
    page_id, view_id = parse_notion_url(url)
    session = requests.Session()

    def post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = session.post(f"{NOTION_API}/{endpoint}", json=payload, headers=_HEADERS, timeout=30)
        response.raise_for_status()
        return response.json()

    page = post("loadPageChunk", {"pageId": page_id, "limit": 50, "chunkNumber": 0, "verticalColumns": False})
    record_map = page.get("recordMap", {})
    collections = record_map.get("collection", {})
    views = record_map.get("collection_view", {})
    if not collections:
        raise ValueError(f"No collection found on Notion page {page_id} (is it public?)")
    collection = next(iter(collections.values()))["value"]["value"]
    collection_id = collection["id"]
    space_id = collection.get("space_id") or next(iter(collections.values())).get("spaceId", "")
    schema = collection.get("schema", {})
    if view_id is None and views:
        view_id = next(iter(views.values()))["value"]["value"]["id"]
    if view_id is None:
        raise ValueError(f"No collection view found on Notion page {page_id}")

    result = post(
        "queryCollection",
        {
            "collection": {"id": collection_id, "spaceId": space_id},
            "collectionView": {"id": view_id, "spaceId": space_id},
            "loader": {
                "type": "reducers",
                "reducers": {"collection_group_results": {"type": "results", "limit": MAX_ROWS}},
                "searchQuery": "",
                "userTimeZone": "America/Los_Angeles",
            },
            "query": {"filter": {"filters": [], "operator": "and"}, "sort": []},
        },
    )
    return jobs_from_record_map(result.get("recordMap", {}), schema, site=site)
