import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from job_email import render_email_subject
from job_sources import load_source_specs, resolve_specs
from job_sources.notion import jobs_from_record_map, parse_notion_url, parse_salary_range
from search_jobs import cron_profiles, filter_jobs, load_profile, score_jobs


class NotionUrlTest(unittest.TestCase):
    def test_parses_page_and_view(self):
        page, view = parse_notion_url(
            "https://pyrite-farmhouse-947.notion.site/2b55dfe3e657804bafa3d2ae46dfd587?v=2b55dfe3e6578187baf4000c46595218"
        )
        self.assertEqual(page, "2b55dfe3-e657-804b-afa3-d2ae46dfd587")
        self.assertEqual(view, "2b55dfe3-e657-8187-baf4-000c46595218")

    def test_salary_range(self):
        self.assertEqual(parse_salary_range("$180K - $220K"), (180_000, 220_000))
        self.assertEqual(parse_salary_range("$150,000"), (150_000, 150_000))
        self.assertEqual(parse_salary_range(""), (None, None))

    def test_jobs_from_record_map(self):
        schema = {
            "title": {"name": "Job Title", "type": "title"},
            "a": {"name": "Locations", "type": "multi_select"},
            "b": {"name": "Description", "type": "text"},
            "c": {"name": "Salary (Base)", "type": "text"},
            "d": {"name": "Tech Stack", "type": "text"},
            "e": {"name": "Team Size", "type": "text"},
            "f": {"name": "Workplace", "type": "text"},
        }
        record_map = {
            "block": {
                "row1": {
                    "value": {
                        "value": {
                            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                            "type": "page",
                            "created_time": 1786237608150,
                            "properties": {
                                "title": [["Full Stack Engineer"]],
                                "a": [["San Francisco"]],
                                "b": [["Build civic dashboards"]],
                                "c": [["$150K - $200K"]],
                                "d": [["React, TypeScript"]],
                                "e": [["40"]],
                                "f": [["Remote"]],
                            },
                        }
                    }
                }
            }
        }
        jobs = jobs_from_record_map(record_map, schema, site="notion-test")
        self.assertEqual(len(jobs), 1)
        row = jobs.iloc[0]
        self.assertEqual(row["title"], "Full Stack Engineer")
        self.assertEqual(row["min_amount"], 150_000)
        self.assertEqual(row["max_amount"], 200_000)
        self.assertTrue(row["is_remote"])
        self.assertIn("Team Size: 40", row["description"])
        self.assertEqual(row["site"], "notion-test")


class SourceRegistryTest(unittest.TestCase):
    def test_repo_sources_toml(self):
        specs = load_source_specs()
        by_name = {spec.name: spec for spec in specs}
        self.assertIn("jobspy", by_name)
        self.assertEqual(by_name["notion-transformer-talent"].type, "notion")
        resolved = resolve_specs(specs, ["jobspy", "notion-transformer-talent", "missing"])
        self.assertEqual([s.name for s in resolved], ["jobspy", "notion-transformer-talent"])


class ProfileFilterTest(unittest.TestCase):
    def test_new_profiles_exclude_startups_and_accept_remote_or_bay_area(self):
        for path in ("profiles/full-stack.example.json", "profiles/applied-math.example.json"):
            profile = load_profile(path)
            self.assertTrue(profile.exclude_startups, path)
            self.assertTrue(profile.remote_ok, path)
            self.assertIn("notion-transformer-talent", profile.sources, path)
            jobs = pd.DataFrame(
                [
                    {"title": "Founding Engineer", "company": "X", "description": "React optimization operations research", "location": "San Francisco", "is_remote": False},
                    {"title": "Engineer", "company": "Y", "description": "React optimization operations research", "location": "San Francisco", "is_remote": False},
                    {"title": "Engineer", "company": "Z", "description": "React optimization operations research", "location": "New York", "is_remote": False},
                    {"title": "Engineer", "company": "W", "description": "React optimization operations research Team Size: 40", "location": "Remote", "is_remote": True},
                ]
            )
            kept = filter_jobs(jobs, profile, apply_location=True)
            # Founding title, New York location, and tiny-team rows are dropped.
            self.assertEqual(kept["company"].tolist(), ["Y"], path)
            scored = score_jobs(kept, profile)
            self.assertGreaterEqual(int(scored.iloc[0]["match_score"]), 1, path)


class EmailSubjectTest(unittest.TestCase):
    def test_subject_uses_profile_name(self):
        # Anonymized examples only: real personal profiles live outside the repo.
        full_stack = load_profile("profiles/full-stack.example.json")
        applied_math = load_profile("profiles/applied-math.example.json")
        self.assertFalse(full_stack.email)
        self.assertFalse(applied_math.email)
        self.assertIn(f"for {full_stack.name}", render_email_subject(full_stack, 3, full_stack.primary_search_term()))
        self.assertIn(f"for {applied_math.name}", render_email_subject(applied_math, 3, applied_math.primary_search_term()))


class CronProfilesTest(unittest.TestCase):
    def test_profiles_list_and_legacy_single(self):
        self.assertEqual(
            cron_profiles({"jobs": {"profiles": ["a.json", "b.json"]}}), ["a.json", "b.json"]
        )
        self.assertEqual(cron_profiles({"jobs": {"profile": "a.json"}}), ["a.json"])
        self.assertEqual(cron_profiles({"jobs": {}}), [])


if __name__ == "__main__":
    unittest.main()
