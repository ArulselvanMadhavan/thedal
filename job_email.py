"""Render and send job search digests via Resend."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

import pandas as pd
import requests

if TYPE_CHECKING:
    from search_jobs import Profile

RESEND_URL = "https://api.resend.com/emails"


def escape(text: object) -> str:
    return html.escape(str(text or ""))


def render_email_subject(profile: Profile, job_count: int, search_term: str) -> str:
    name = profile.name or profile.current_title or "profile"
    return f"thedal — {job_count} jobs for {name} ({search_term})"


def render_email_html(profile: Profile, jobs: pd.DataFrame, search_term: str) -> str:
    summary = jobs.copy()
    if "salary" not in summary.columns and not jobs.empty:
        from search_jobs import add_summary_columns

        summary = add_summary_columns(jobs)

    rows: list[str] = []
    for _, row in summary.iterrows():
        title = escape(row.get("title", "Untitled"))
        url = escape(row.get("job_url", "") or row.get("job_url_direct", "") or "#")
        company = escape(row.get("company", ""))
        location = escape(row.get("location", ""))
        job_type = escape(row.get("job_type", "") or "—")
        salary = escape(row.get("salary", "") or "—")
        site = escape(row.get("site", ""))
        posted = escape(row.get("date_posted", "") or "")
        score = escape(int(row.get("match_score", 0)))
        rows.append(
            "<tr>"
            f"<td>{score}</td>"
            f"<td>{site}</td>"
            f'<td><a href="{url}">{title}</a></td>'
            f"<td>{company}</td>"
            f"<td>{location}</td>"
            f"<td>{job_type}</td>"
            f"<td>{salary}</td>"
            f"<td>{posted}</td>"
            "</tr>"
        )

    profile_line = " · ".join(
        escape(bit)
        for bit in [profile.name, profile.headline, profile.location, search_term]
        if bit
    )
    forward_line = ""
    if profile.email:
        forward_line = (
            f"<p><em>Intended for {escape(profile.name or 'profile')} "
            f"({escape(profile.email)}). Please forward.</em></p>"
        )
    table_rows = "\n".join(rows) if rows else "<tr><td colspan='8'>No jobs found.</td></tr>"

    return f"""<!DOCTYPE html>
<html>
<body>
  <h1>Job search results</h1>
  <p>{profile_line}</p>
  {forward_line}
  <p>{len(jobs)} jobs</p>
  <table border="1" cellpadding="4" cellspacing="0">
    <tr>
      <th>Score</th><th>Site</th><th>Title</th><th>Company</th>
      <th>Location</th><th>Type</th><th>Salary</th><th>Posted</th>
    </tr>
    {table_rows}
  </table>
</body>
</html>"""


def send_email(
    *,
    api_key: str,
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    html_body: str,
) -> str:
    if not to_addrs:
        raise ValueError("no recipients configured")
    if not from_addr:
        raise ValueError("resend from address not configured")
    if not api_key.startswith("re_"):
        raise ValueError("invalid Resend API key")

    response = requests.post(
        RESEND_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": from_addr,
            "to": to_addrs,
            "subject": subject,
            "html": html_body,
        },
        timeout=30,
    )
    if not response.ok:
        message = response.text.strip() or response.reason
        raise RuntimeError(f"Resend request failed: {message}")

    payload = response.json()
    message_id = payload.get("id")
    if not message_id:
        raise RuntimeError(f"unexpected Resend response: {response.text}")
    return str(message_id)
