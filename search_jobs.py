#!/usr/bin/env python3
"""Search job boards for roles matching a candidate profile."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

JOBS_DIR = Path("jobs")
SUMMARY_COLUMNS = [
    "match_score",
    "site",
    "title",
    "company",
    "location",
    "job_type",
    "salary",
    "date_posted",
    "job_url",
]

import pandas as pd
import requests
from bs4 import BeautifulSoup
from jobspy import scrape_jobs
from job_email import render_email_html, render_email_subject, send_email


@dataclass
class Profile:
    name: str = ""
    headline: str = ""
    location: str = ""
    current_title: str = ""
    past_titles: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    company_search_terms: list[str] = field(default_factory=list)
    match_keywords: list[str] = field(default_factory=list)
    linkedin_company_ids: list[int] = field(default_factory=list)
    min_match_score: int = 0
    search_all_terms: bool = False
    email: str = ""
    # Source names from sources.toml (empty = JobSpy boards only).
    sources: list[str] = field(default_factory=list)
    # Extra search locations, e.g. ["San Francisco, California", "Remote, United States"].
    locations: list[str] = field(default_factory=list)
    remote_ok: bool = False
    # Location tokens accepted from curated (non-JobSpy) sources.
    location_keywords: list[str] = field(default_factory=list)
    # Drop a job when any of these appears in title/company/description.
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_startups: bool = False
    # Slow-moving / preferred industries: each match boosts match_score by 1.
    preferred_keywords: list[str] = field(default_factory=list)

    def primary_search_term(self) -> str:
        if self.search_terms:
            return self.search_terms[0]
        if self.current_title:
            return self.current_title
        if self.headline:
            return self.headline.split("·")[0].strip()
        if self.past_titles:
            return self.past_titles[0]
        return "software engineer"

    def skill_keywords(self) -> list[str]:
        keywords: list[str] = []
        for skill in self.skills:
            normalized = skill.strip()
            if normalized and normalized not in keywords:
                keywords.append(normalized)
        for fragment in re.split(r"[·|,]", self.headline):
            token = fragment.strip()
            if len(token) > 2 and token not in keywords:
                keywords.append(token)
        return keywords

    def relevance_keywords(self) -> list[str]:
        if self.match_keywords:
            return [kw.strip() for kw in self.match_keywords if kw.strip()]
        return self.skill_keywords()


def load_profile(source: str) -> Profile:
    """Load a profile from a URL, JSON file, or HTML file."""
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return parse_portfolio_html(fetch_html(source))

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Profile source not found: {source}")

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return parse_profile_json(json.loads(text))
    if "<html" in text.lower():
        return parse_portfolio_html(text)
    raise ValueError(f"Unsupported profile file type: {path}")


def fetch_html(url: str) -> str:
    response = requests.get(url, timeout=30, headers={"User-Agent": "job-search-script/1.0"})
    response.raise_for_status()
    return response.text


def parse_profile_json(data: dict[str, Any]) -> Profile:
    return Profile(
        name=data.get("name", ""),
        headline=data.get("headline", ""),
        location=data.get("location", ""),
        current_title=data.get("current_title", ""),
        past_titles=list(data.get("past_titles", [])),
        skills=list(data.get("skills", [])),
        search_terms=list(data.get("search_terms", [])),
        company_search_terms=list(data.get("company_search_terms", [])),
        match_keywords=list(data.get("match_keywords", [])),
        linkedin_company_ids=[int(value) for value in data.get("linkedin_company_ids", [])],
        min_match_score=int(data.get("min_match_score", 0)),
        search_all_terms=bool(data.get("search_all_terms", False)),
        email=data.get("email", ""),
        sources=list(data.get("sources", [])),
        locations=list(data.get("locations", [])),
        remote_ok=bool(data.get("remote_ok", False)),
        location_keywords=list(data.get("location_keywords", [])),
        exclude_keywords=list(data.get("exclude_keywords", [])),
        exclude_startups=bool(data.get("exclude_startups", False)),
        preferred_keywords=list(data.get("preferred_keywords", [])),
    )


def parse_portfolio_html(html: str) -> Profile:
    soup = BeautifulSoup(html, "html.parser")

    name = soup.find("h1")
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    headline = paragraphs[0] if paragraphs else ""
    location = paragraphs[1] if len(paragraphs) > 1 else ""

    skills: list[str] = []
    skills_heading = soup.find("h2", string=re.compile(r"^Skills$", re.I))
    if skills_heading:
        section = skills_heading.find_parent("section") or skills_heading.parent
        skills = [li.get_text(strip=True) for li in section.find_all("li")]

    past_titles: list[str] = []
    timeline_heading = soup.find("h2", string=re.compile(r"^Timeline$", re.I))
    if timeline_heading:
        section = timeline_heading.find_parent("section") or timeline_heading.parent
        past_titles = [h3.get_text(strip=True) for h3 in section.find_all("h3")]

    current_title = past_titles[0] if past_titles else headline.split("·")[0].strip()

    return Profile(
        name=name.get_text(strip=True) if name else "",
        headline=headline,
        location=location,
        current_title=current_title,
        past_titles=past_titles,
        skills=skills,
    )


STARTUP_SIGNALS = [
    "founding engineer",
    "founding member",
    "founding team",
    "co-founder",
    "cofounder",
    "startup",
    "start-up",
    "stealth mode",
    "stealth startup",
    "pre-seed",
    "preseed",
    "seed stage",
    "seed-stage",
    "early-stage company",
    "early stage company",
]


def _combined_text(jobs: pd.DataFrame) -> pd.Series:
    def column(name: str) -> pd.Series:
        return jobs.get(name, pd.Series("", index=jobs.index)).fillna("").astype(str)

    return (column("title") + " " + column("company") + " " + column("description")).str.lower()


def filter_jobs(jobs: pd.DataFrame, profile: Profile, *, apply_location: bool = False) -> pd.DataFrame:
    """Drop jobs violating the profile's exclusion / location preferences."""
    if jobs.empty:
        return jobs
    combined = _combined_text(jobs)
    keep = pd.Series(True, index=jobs.index)

    excluded = [kw.lower() for kw in profile.exclude_keywords if kw.strip()]
    if profile.exclude_startups:
        excluded += STARTUP_SIGNALS
    for keyword in excluded:
        keep &= ~combined.str.contains(re.escape(keyword), regex=False)

    if profile.exclude_startups:
        # Curated sources expose "Team Size: N"; a tiny team is a startup signal
        # even when the posting never says the word "startup".
        team_sizes = combined.str.extract(r"team size:\s*(\d+)", expand=False)
        keep &= ~(team_sizes.astype(float) < 200)

    if apply_location and profile.location_keywords:
        location_series = jobs.get("location", pd.Series("", index=jobs.index)).fillna("").astype(str).str.lower()
        tokens = [token.lower() for token in profile.location_keywords if token.strip()]
        location_ok = location_series.map(lambda loc: any(token in loc for token in tokens))
        if profile.remote_ok and "is_remote" in jobs.columns:
            location_ok |= jobs["is_remote"].fillna(False).astype(bool)
        keep &= location_ok

    return jobs[keep].reset_index(drop=True)


def score_jobs(jobs: pd.DataFrame, profile: Profile) -> pd.DataFrame:
    if jobs.empty:
        return jobs

    keywords = [kw.lower() for kw in profile.relevance_keywords()]
    preferred = [kw.lower() for kw in profile.preferred_keywords if kw.strip()]
    combined = _combined_text(jobs)

    def count_matches(text: str) -> int:
        return sum(1 for keyword in keywords if keyword in text) + sum(
            1 for keyword in preferred if keyword in text
        )

    scored = jobs.copy()
    if "date_posted" not in scored.columns:
        scored["date_posted"] = ""
    scored["match_score"] = combined.map(count_matches)
    scored = scored.sort_values(["match_score", "date_posted"], ascending=[False, False], na_position="last")

    if profile.min_match_score > 0:
        scored = scored[scored["match_score"] >= profile.min_match_score]

    return scored.reset_index(drop=True)


def merge_job_results(frames: list[pd.DataFrame]) -> pd.DataFrame:
    valid = [frame for frame in frames if not frame.empty]
    if not valid:
        return pd.DataFrame()
    merged = pd.concat(valid, ignore_index=True)
    if "job_url" in merged.columns:
        merged = merged.drop_duplicates(subset=["job_url"], keep="first")
    return merged.reset_index(drop=True)


def scrape_once(
    *,
    sites: list[str],
    search_term: str,
    location: str | None,
    results_wanted: int,
    hours_old: int | None,
    is_remote: bool,
    proxies: list[str] | None,
    enforce_annual_salary: bool,
    linkedin_company_ids: list[int] | None,
) -> pd.DataFrame:
    kwargs: dict[str, Any] = {
        "site_name": sites,
        "search_term": search_term,
        "location": location or None,
        "results_wanted": results_wanted,
        "hours_old": hours_old,
        "is_remote": is_remote,
        "country_indeed": "USA",
        "proxies": proxies,
        "enforce_annual_salary": enforce_annual_salary,
        "verbose": 1,
    }
    if linkedin_company_ids:
        kwargs["linkedin_company_ids"] = linkedin_company_ids
    return scrape_jobs(**kwargs)


def search_passes(profile: Profile, location: str | None, is_remote: bool) -> list[tuple[str, bool]]:
    """(location, is_remote) passes: profile.locations, else its single location.

    A location mentioning "remote" (with profile.remote_ok) runs as a
    remote-only pass; --remote forces every pass remote.
    """
    if location:
        return [(location, is_remote)]
    locations = list(profile.locations or ([profile.location] if profile.location else []))
    passes: list[tuple[str, bool]] = []
    for loc in locations:
        remote_pass = is_remote or (profile.remote_ok and "remote" in loc.lower())
        if (loc, remote_pass) not in passes:
            passes.append((loc, remote_pass))
    return passes or [("", is_remote)]


def search_jobs(
    profile: Profile,
    *,
    location: str | None,
    search_term: str | None,
    sites: list[str],
    results_wanted: int,
    hours_old: int | None,
    is_remote: bool,
    proxies: list[str] | None = None,
    enforce_annual_salary: bool = False,
    sources_file: str | None = None,
    source_names: list[str] | None = None,
) -> pd.DataFrame:
    role_terms = [search_term] if search_term else list(profile.search_terms or [profile.primary_search_term()])
    if profile.search_all_terms and not search_term:
        role_terms = list(profile.search_terms or role_terms)

    from job_sources import fetch_source, load_source_specs, resolve_specs

    wanted = source_names or profile.sources or ["jobspy"]
    specs = resolve_specs(load_source_specs(sources_file), wanted)
    print(f"Sources: {', '.join(spec.name for spec in specs) or '(none)'}")
    print(f"Profile keywords for ranking/filtering: {len(profile.relevance_keywords())}")
    if profile.min_match_score > 0:
        print(f"Minimum match score: {profile.min_match_score}")
    if proxies:
        print(f"Proxies: {len(proxies)} configured")
    if enforce_annual_salary:
        print("Salary normalization: annual")

    frames: list[pd.DataFrame] = []
    if any(spec.is_jobspy for spec in specs):
        print(f"Sites: {', '.join(sites)}")
        linkedin_sites = [site for site in sites if site == "linkedin"]
        other_sites = [site for site in sites if site != "linkedin"]
        for job_location, remote_pass in search_passes(profile, location, is_remote):
            if linkedin_sites and profile.linkedin_company_ids:
                for role_term in role_terms:
                    print(f"LinkedIn company search: {role_term!r} @ {len(profile.linkedin_company_ids)} companies")
                    frames.append(
                        scrape_once(
                            sites=linkedin_sites,
                            search_term=role_term,
                            location=job_location,
                            results_wanted=results_wanted,
                            hours_old=hours_old,
                            is_remote=remote_pass,
                            proxies=proxies,
                            enforce_annual_salary=enforce_annual_salary,
                            linkedin_company_ids=profile.linkedin_company_ids,
                        )
                    )
            elif linkedin_sites:
                for role_term in role_terms:
                    print(f"Searching for: {role_term!r} in {job_location!r} (remote={remote_pass})")
                    frames.append(
                        scrape_once(
                            sites=linkedin_sites,
                            search_term=role_term,
                            location=job_location,
                            results_wanted=results_wanted,
                            hours_old=hours_old,
                            is_remote=remote_pass,
                            proxies=proxies,
                            enforce_annual_salary=enforce_annual_salary,
                            linkedin_company_ids=None,
                        )
                    )

            company_terms = profile.company_search_terms or []
            if other_sites:
                terms = company_terms or role_terms
                for term in terms:
                    print(f"Searching for: {term!r} in {job_location!r} (remote={remote_pass})")
                    frames.append(
                        scrape_once(
                            sites=other_sites,
                            search_term=term,
                            location=job_location,
                            results_wanted=results_wanted,
                            hours_old=hours_old,
                            is_remote=remote_pass,
                            proxies=proxies,
                            enforce_annual_salary=enforce_annual_salary,
                            linkedin_company_ids=None,
                        )
                    )

    jobspy_jobs = score_jobs(filter_jobs(merge_job_results(frames), profile), profile)

    external_frames: list[pd.DataFrame] = []
    for spec in specs:
        if spec.is_jobspy:
            continue
        try:
            print(f"Fetching source: {spec.name} ({spec.type})")
            fetched = fetch_source(spec)
        except Exception as exc:  # one bad source must not sink the whole run
            print(f"Warning: source {spec.name!r} failed: {exc}")
            continue
        print(f"  {len(fetched)} jobs before profile filtering")
        external_frames.append(score_jobs(filter_jobs(fetched, profile, apply_location=True), profile))

    return merge_job_results([jobspy_jobs, *external_frames]).pipe(
        lambda df: df.sort_values(["match_score", "date_posted"], ascending=[False, False], na_position="last").reset_index(drop=True)
        if not df.empty and "match_score" in df.columns
        else df
    )


def load_cron_config() -> dict[str, Any]:
    state_dir = Path(os.environ.get("THEDAL_STATE_DIR", "/lm/users/arul/thedal-state"))
    config_path = state_dir / "cron.toml"
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        return tomllib.load(handle)


def cron_profiles(config: dict[str, Any]) -> list[str]:
    """All profiles cron should run, one email each.

    Supports ``profiles = ["a.json", "b.json"]`` and the legacy single
    ``profile = "a.json"`` in cron.toml [jobs].
    """
    jobs_cfg = config.get("jobs", {})
    profiles = jobs_cfg.get("profiles")
    if isinstance(profiles, list):
        return [str(value).strip() for value in profiles if str(value).strip()]
    single = str(jobs_cfg.get("profile", "")).strip()
    return [single] if single else []


def digest_recipients(config: dict[str, Any]) -> list[str]:
    """Resend-verified recipient(s) from cron.toml (same role as elderberry digest.to)."""
    to_value = config.get("jobs", {}).get("to", "")
    if isinstance(to_value, list):
        return [str(value).strip() for value in to_value if str(value).strip()]
    return [part.strip() for part in str(to_value).split(",") if part.strip()]


def read_api_key_stdin() -> str:
    key = sys.stdin.read().strip()
    if not key:
        raise ValueError("empty API key on stdin")
    if not key.startswith("re_"):
        raise ValueError("API key should start with re_")
    return key


def apply_cron_defaults(args: argparse.Namespace) -> None:
    config = load_cron_config()
    jobs_cfg = config.get("jobs", {})
    if args.results == 10 and "results" in jobs_cfg:
        args.results = int(jobs_cfg["results"])
    if args.sites == "indeed,linkedin" and "sites" in jobs_cfg:
        args.sites = str(jobs_cfg["sites"])
    if args.hours_old == 168 and "hours_old" in jobs_cfg:
        args.hours_old = int(jobs_cfg["hours_old"])


def slugify(text: str, fallback: str = "profile") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or fallback


def default_output_stem(profile: Profile, search_term: str | None) -> Path:
    label = profile.name or profile.current_title or search_term or "jobs"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return JOBS_DIR / f"{slugify(label)}-{timestamp}"


def format_salary(row: pd.Series) -> str:
    min_amount = row.get("min_amount")
    max_amount = row.get("max_amount")
    if pd.isna(min_amount) and pd.isna(max_amount):
        return ""

    currency = row.get("currency") if not pd.isna(row.get("currency")) else "USD"
    symbol = "$" if currency == "USD" else f"{currency} "

    interval = row.get("interval")
    if pd.isna(interval):
        suffix = ""
    elif interval == "yearly":
        suffix = "/yr"
    elif interval == "hourly":
        suffix = "/hr"
    elif interval == "monthly":
        suffix = "/mo"
    else:
        suffix = f"/{interval}"

    def fmt(amount: float) -> str:
        if pd.isna(amount):
            return ""
        if float(amount).is_integer():
            return f"{symbol}{int(amount):,}"
        return f"{symbol}{float(amount):,.2f}"

    low = fmt(min_amount)
    high = fmt(max_amount)
    if low and high:
        if low == high:
            return f"{low}{suffix}"
        return f"{low}-{high}{suffix}"
    return low or high


def add_summary_columns(jobs: pd.DataFrame) -> pd.DataFrame:
    summary = jobs.copy()
    summary["salary"] = summary.apply(format_salary, axis=1)
    return summary


def terminal_link(url: str, text: str) -> str:
    if not url or pd.isna(url):
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def print_summary(profile: Profile, jobs: pd.DataFrame) -> None:
    print()
    if profile.name:
        print(f"Profile: {profile.name}")
    if profile.headline:
        print(f"Headline: {profile.headline}")
    if profile.current_title:
        print(f"Current title: {profile.current_title}")
    print(f"Found {len(jobs)} jobs")
    print()

    if jobs.empty:
        return

    summary = add_summary_columns(jobs.head(20))
    headers = ["score", "site", "title", "company", "location", "type", "salary", "posted"]
    widths = [5, 8, 42, 18, 22, 10, 22, 10]

    def row_values(row: pd.Series) -> list[str]:
        title = truncate(str(row.get("title", "")), widths[2])
        url = str(row.get("job_url", "") or row.get("job_url_direct", "") or "")
        return [
            str(int(row.get("match_score", 0))),
            truncate(str(row.get("site", "")), widths[1]),
            terminal_link(url, title),
            truncate(str(row.get("company", "")), widths[3]),
            truncate(str(row.get("location", "")), widths[4]),
            truncate(str(row.get("job_type", "") or ""), widths[5]),
            truncate(str(row.get("salary", "") or "—"), widths[6]),
            truncate(str(row.get("date_posted", "") or ""), widths[7]),
        ]

    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(header_line)
    print("-" * len(header_line))
    for _, row in summary.iterrows():
        values = row_values(row)
        print("  ".join(pad_visible(values[i], widths[i]) for i in range(len(headers))))


def save_jobs(
    jobs: pd.DataFrame,
    stem_path: Path,
) -> dict[str, Path]:
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    summary = add_summary_columns(jobs)

    csv_path = stem_path.with_suffix(".csv")
    slim = summary.drop(columns=["description", "company_description"], errors="ignore")
    slim_cols = [col for col in SUMMARY_COLUMNS if col in slim.columns]
    slim[slim_cols].to_csv(csv_path, index=False)

    json_path = stem_path.with_suffix(".json")
    json_path.write_text(
        summary.to_json(orient="records", indent=2, date_format="iso"),
        encoding="utf-8",
    )

    return {"csv": csv_path, "json": json_path}


INVISIBLE_ESCAPES = re.compile(r"\033(?:\[[0-9;]*m|\][^\n]*?\033\\)")


def pad_visible(text: str, width: int) -> str:
    visible_len = len(INVISIBLE_ESCAPES.sub("", text))
    return text + (" " * max(width - visible_len, 0))


def output_stem_from_arg(path: str | None, profile: Profile, search_term: str) -> Path:
    if not path:
        return default_output_stem(profile, search_term)
    stem = Path(path)
    if stem.suffix:
        return stem.with_suffix("")
    return stem


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search job boards for roles matching a candidate profile.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "profile",
        help="Profile URL, JSON file, or HTML portfolio file",
    )
    parser.add_argument(
        "--search-term",
        help="Override the search query derived from the profile",
    )
    parser.add_argument(
        "--location",
        help="Override the location derived from the profile",
    )
    parser.add_argument(
        "--sites",
        default="indeed,linkedin",
        help="Comma-separated job sites",
    )
    parser.add_argument(
        "--results",
        type=int,
        default=10,
        help="Number of results to fetch per site",
    )
    parser.add_argument(
        "--hours-old",
        type=int,
        default=168,
        help="Only include jobs posted within this many hours",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Restrict search to remote jobs",
    )
    parser.add_argument(
        "--proxies",
        help="Comma-separated proxy URLs (host:port or user:pass@host:port)",
    )
    parser.add_argument(
        "--enforce-annual-salary",
        action="store_true",
        help="Convert hourly/monthly wages to annual min_amount/max_amount",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Override output file stem under jobs/ (writes .csv and .json)",
    )
    parser.add_argument(
        "--sources",
        help="Comma-separated source names from sources.toml (default: profile's sources, else jobspy)",
    )
    parser.add_argument(
        "--sources-file",
        help="Path to the sources registry TOML (default: ./sources.toml)",
    )
    parser.add_argument(
        "--cron",
        action="store_true",
        help="Cron mode: apply cron.toml defaults and allow email send",
    )
    parser.add_argument(
        "--email",
        action="store_true",
        help="Email results via Resend (requires --cron; sends to jobs.to in cron.toml)",
    )
    parser.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="Read Resend API key from stdin (used by jobs-cron.sh)",
    )
    parser.add_argument(
        "--preview-email",
        action="store_true",
        help="Print HTML email to stdout without sending",
    )
    return parser


def parse_proxies(value: str | None) -> list[str] | None:
    if not value:
        return None
    proxies = [proxy.strip() for proxy in value.split(",") if proxy.strip()]
    return proxies or None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.email and not args.cron:
        print("--email requires --cron", file=sys.stderr)
        return 2
    if args.api_key_stdin and not args.cron:
        print("--api-key-stdin requires --cron", file=sys.stderr)
        return 2
    if args.cron:
        apply_cron_defaults(args)

    try:
        profile = load_profile(args.profile)
    except Exception as exc:
        print(f"Failed to load profile: {exc}", file=sys.stderr)
        return 1

    sites = [site.strip() for site in args.sites.split(",") if site.strip()]
    try:
        jobs = search_jobs(
            profile,
            location=args.location,
            search_term=args.search_term,
            sites=sites,
            results_wanted=args.results,
            hours_old=args.hours_old,
            is_remote=args.remote,
            proxies=parse_proxies(args.proxies),
            enforce_annual_salary=args.enforce_annual_salary,
            sources_file=args.sources_file,
            source_names=[s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None,
        )
    except Exception as exc:
        print(f"Job search failed: {exc}", file=sys.stderr)
        return 1

    if not args.cron:
        print_summary(profile, jobs)

    query = args.search_term or profile.primary_search_term()
    stem_path = output_stem_from_arg(args.output, profile, query)
    outputs = save_jobs(jobs, stem_path)
    print(
        f"\nWrote {len(jobs)} jobs to:\n"
        f"  {outputs['csv']}  (summary, no descriptions)\n"
        f"  {outputs['json']}  (full data)"
    )

    if args.preview_email or args.email:
        cron_config = load_cron_config()
        recipients = digest_recipients(cron_config)
        if not recipients:
            print(
                'No digest recipient configured; add jobs.to in cron.toml '
                "(Resend-verified address, like elderberry digest.to).",
                file=sys.stderr,
            )
            return 1

        html_body = render_email_html(profile, jobs, query)
        subject = render_email_subject(profile, len(jobs), query)
        if args.preview_email:
            print("\n--- email preview ---")
            print(f"To: {', '.join(recipients)}")
            if profile.email:
                print(f"Intended for: {profile.name or 'profile'} <{profile.email}> (forward manually)")
            print(f"Subject: {subject}")
            print(html_body)
            return 0

        from_addr = cron_config.get("resend", {}).get("from", "onboarding@resend.dev")
        try:
            api_key = read_api_key_stdin() if args.api_key_stdin else ""
            if not api_key:
                print("Missing Resend API key (--api-key-stdin required in cron mode).", file=sys.stderr)
                return 1
            message_id = send_email(
                api_key=api_key,
                from_addr=from_addr,
                to_addrs=recipients,
                subject=subject,
                html_body=html_body,
            )
        except Exception as exc:
            print(f"Email failed: {exc}", file=sys.stderr)
            return 1
        print(f"Sent email {message_id} to {', '.join(recipients)}")
        if profile.email:
            print(f"Intended for {profile.name or 'profile'} <{profile.email}> — forward manually if needed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
