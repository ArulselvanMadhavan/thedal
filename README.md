# Job Search

Search job boards for roles that match a candidate profile. Profiles can come from a portfolio URL, a JSON file, or a local HTML page. Results are scraped with [python-jobspy](https://pypi.org/project/python-jobspy/), ranked by skill overlap, printed to the terminal, and saved as CSV under `jobs/` (gitignored).

## Setup

This project uses Python 3.13 via pyenv (see `.python-version`).

```bash
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
pyenv init - | source /dev/stdin

cd /data/home/arul/dev/thedal
pip install -r requirements.txt
```

## Quick start

```bash
python search_jobs.py profiles/data-engineer.example.json
```

Or pass a portfolio URL directly:

```bash
python search_jobs.py https://example.com/portfolio/
```

Each run writes two files to `jobs/`:

```text
jobs/example-candidate-data-engineer-20260720-023643.csv   # compact summary (no descriptions)
jobs/example-candidate-data-engineer-20260720-023643.json  # full data including descriptions
```

## Profile inputs

The `profile` argument accepts three formats.

### 1. Portfolio URL

Fetches the page and extracts name, headline, location, skills, and job titles from common portfolio sections (`Skills`, `Timeline`).

```bash
python search_jobs.py https://example.com/portfolio/
```

### 2. JSON file

Use a structured profile for offline use or fine-grained control. See `profiles/data-engineer.example.json` for an example.

```bash
python search_jobs.py profiles/data-engineer.example.json
```

Supported fields:

| Field | Purpose |
| --- | --- |
| `name` | Used for output filenames |
| `headline` | Fallback for search terms and skill keywords |
| `location` | Default job search location |
| `current_title` | Primary search query if `search_terms` is omitted |
| `past_titles` | Fallback titles |
| `skills` | Used to rank results by keyword overlap |
| `search_terms` | Optional override list; role keywords used for job board searches |
| `company_search_terms` | Company names searched on non-LinkedIn boards (e.g. Indeed) |
| `linkedin_company_ids` | LinkedIn numeric company IDs — restricts LinkedIn results to those employers |
| `match_keywords` | Keywords used for ranking/filtering instead of `skills` when set |
| `min_match_score` | Drop results below this score (useful for targeted profiles) |
| `search_all_terms` | Run every entry in `search_terms` and merge results |
| `email` | Intended recipient (shown in email body; forward manually from `jobs.to`) |
| `sources` | Source names from `sources.toml` (omit = JobSpy boards only) |
| `locations` | Extra search locations; a "Remote…" entry + `remote_ok` adds a remote-only pass |
| `remote_ok` | Accept remote jobs (curated sources: keep rows marked remote) |
| `location_keywords` | Location tokens accepted from curated sources (e.g. `remote`, `san francisco`) |
| `exclude_keywords` | Drop a job if any keyword appears in title/company/description |
| `exclude_startups` | Drop startup-signal jobs (`founding`, `startup`, `stealth`, …, or `Team Size: <200` from curated sources) |
| `preferred_keywords` | Preferred (e.g. slow-moving) industries; each match boosts `match_score` by 1 |

Example profiles: `profiles/full-stack.example.json` (full-stack, established/civic-tech employers, remote + Bay Area) and `profiles/applied-math.example.json` (applied mathematician, optimization specialization, same location/startup preferences). **Personal profiles — with real names/emails — must never be committed (this repo is public):** keep them in `$THEDAL_STATE_DIR/profiles/` (gitignored) and reference them by absolute path in the state-dir `cron.toml`. The profile `name` drives output filenames and the email subject (`thedal — N jobs for <name> (…)`); the `email` field labels the intended recipient.

Example:

```json
{
  "name": "Example Candidate",
  "headline": "Machine Systems Engineer · ML Performance · Functional Programming",
  "location": "San Francisco, California",
  "current_title": "Machine Systems Engineer",
  "skills": ["Python", "CUDA", "PyTorch"],
  "search_terms": ["machine systems engineer"]
}
```

### 3. Local HTML file

Any saved portfolio or CV page with similar structure is parsed the same way as a URL.

```bash
python search_jobs.py ~/Downloads/portfolio.html
```

## Job sources (standardized)

All sources are registered in **`sources.toml`** and return the same canonical columns (`title`, `company`, `location`, `description`, `job_url`, `site`, `date_posted`, salary fields, …; see `job_sources/base.py`). A profile opts into sources by name:

```json
"sources": ["jobspy", "notion-transformer-talent"]
```

To add a source:

1. **New board of an existing type** (`jobspy`, `notion`): add a `[[sources]]` entry to `sources.toml` with a unique `name`, its `type`, and its config (e.g. `url`). No code needed.
2. **New type of board**: add an adapter in `job_sources/` exposing `fetch_<type>_jobs(config, *, site) -> DataFrame` with canonical columns, register it in `job_sources.ADAPTERS`, then do step 1.
3. List the source `name` in the profile's `sources` (or pass `--sources name1,name2`).

The `notion` adapter reads any **public** Notion collection (like the [Transformer Talent jobs table](https://pyrite-farmhouse-947.notion.site/2b55dfe3e657804bafa3d2ae46dfd587?v=2b55dfe3e6578187baf4000c46595218)) via Notion's unauthenticated web API — no token needed. That board has no company column and is startup-heavy, so `exclude_startups` profiles filter most of its rows out; rows link back to their Notion page. A failing source warns and is skipped rather than sinking the run.

## Usage examples

### Override the search query

```bash
python search_jobs.py profiles/data-engineer.example.json \
  --search-term "ML performance engineer"
```

### Override location

```bash
python search_jobs.py profiles/data-engineer.example.json \
  --location "Mountain View, CA"
```

### Choose job sites

Default is `indeed,linkedin`. Other supported sites include `zip_recruiter`, `glassdoor`, and `google`.

```bash
python search_jobs.py profiles/data-engineer.example.json \
  --sites indeed,linkedin,zip_recruiter
```

### Limit results and recency

```bash
python search_jobs.py profiles/data-engineer.example.json \
  --results 20 \
  --hours-old 72
```

### Remote-only search

```bash
python search_jobs.py profiles/data-engineer.example.json --remote
```

### Search through proxies

Use one or more proxies as a comma-separated list. jobspy rotates through them on each request.

```bash
python search_jobs.py profiles/data-engineer.example.json \
  --proxies "208.195.175.46:65095,208.195.175.45:65095"
```

Authenticated proxy:

```bash
python search_jobs.py profiles/data-engineer.example.json \
  --proxies "user:password@proxy.example.com:8080"
```

Load proxies from a file (one per line):

```bash
python search_jobs.py profiles/data-engineer.example.json \
  --proxies "$(paste -sd, proxies.txt)"
```

Proxies are passed straight through to [python-jobspy](https://pypi.org/project/python-jobspy/) as `proxies=...`.

### Normalize salaries to annual amounts

By default, scraped wages keep their original interval (`hourly`, `monthly`, `yearly`). Pass `--enforce-annual-salary` to convert non-yearly wages into annual `min_amount` / `max_amount` values in the CSV:

```bash
python search_jobs.py profiles/data-engineer.example.json --enforce-annual-salary
```

Relevant CSV columns: `interval`, `min_amount`, `max_amount`, `currency`, `salary_source`.

### Email digest (cron)

Modeled after elderberry's daily digest. Resend delivers to **`jobs.to`** in `cron.toml` (your verified Resend address). The profile **`email`** field labels who the digest is for — forward manually, same as elderberry.

```bash
cp cron.toml.example /lm/users/arul/thedal-state/cron.toml
scripts/install-jobs-cron.sh
scripts/jobs-cron.sh
python search_jobs.py profiles/data-engineer.example.json --preview-email
```

`cron.toml` lists every profile under `[jobs] profiles = [...]`; cron runs each one and sends **a separate email per profile** (the subject names the profile; a failing profile doesn't stop the others). `scripts/jobs-cron.sh --profile $THEDAL_STATE_DIR/profiles/someone.json` runs just one. Output is logged to `$THEDAL_STATE_DIR/logs/cron.log`.

Installed schedule: **daily 06:00 Pacific**. Resend API key is stored in crontab only.

### Custom output path

By default, results go to `jobs/`. Use `-o` to change the file stem (`.csv` and `.json` are both written):

```bash
python search_jobs.py profiles/data-engineer.example.json \
  -o /tmp/my-jobs
```

## Output

**Terminal:** a compact table with score, site, clickable title (OSC 8 hyperlink), company, location, job type, salary, and posted date.

**CSV:** summary columns only (`match_score`, `site`, `title`, `company`, `location`, `job_type`, `salary`, `date_posted`, `job_url`) — no multi-line descriptions.

**JSON:** full scraped records plus `match_score` and formatted `salary`. Descriptions are here; fold fields in your editor or filter programmatically.

The `match_score` counts how many profile keywords appear in each job's title, company, and description.

## CLI reference

```text
usage: search_jobs.py [-h] [--search-term SEARCH_TERM] [--location LOCATION]
                      [--sites SITES] [--results RESULTS]
                      [--hours-old HOURS_OLD] [--remote] [--proxies PROXIES]
                      [--enforce-annual-salary] [--output OUTPUT]
                      profile

positional arguments:
  profile               Profile URL, JSON file, or HTML portfolio file

optional arguments:
  --search-term         Override the search query derived from the profile
  --location            Override the location derived from the profile
  --sites               Comma-separated job sites (default: indeed,linkedin)
  --results             Number of results to fetch per site (default: 10)
  --hours-old           Only include jobs posted within this many hours (default: 168)
  --remote              Restrict search to remote jobs
  --sources             Comma-separated source names from sources.toml
  --sources-file        Path to the sources registry TOML
  --proxies             Comma-separated proxy URLs (host:port or user:pass@host:port)
  --enforce-annual-salary
                        Convert hourly/monthly wages to annual min_amount/max_amount
  --output, -o          Override output file stem (writes .csv and .json)
```
