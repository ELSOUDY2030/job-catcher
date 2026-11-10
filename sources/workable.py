"""Workable's public cross-company job search (jobs.workable.com).

ponytail: undocumented endpoint, may change without notice; this source then fails alone.
"""
import sys
import time
import urllib.parse

from . import countries_this_run, get_json, make_job

API = "https://jobs.workable.com/api/v1/jobs"


def _location(job):
    loc = job.get("location") or {}
    text = ", ".join(p for p in (loc.get("city"), loc.get("countryName")) if p)
    return f"{text} ({job['workplace']})" if job.get("workplace") else text


def fetch(section, cfg):
    jobs = {}
    query = " OR ".join(section["terms"])  # one query per country keeps requests low
    for country in countries_this_run(cfg):
        token = None
        try:
            # Results are sorted by relevance, not date, so read a few pages.
            for _ in range(section.get("max_pages", 3)):
                params = {"query": query, "location": country}
                if token:
                    params["pageToken"] = token
                data = get_json(f"{API}?{urllib.parse.urlencode(params)}")
                for j in data.get("jobs", []):
                    company = (j.get("company") or {}).get("title")
                    jobs[j["url"]] = make_job(j.get("title"), company, _location(j), j["url"],
                                              j.get("created"), "workable",
                                              text=(j.get("description"), j.get("requirementsSection")))
                token = data.get("nextPageToken")
                if not token:
                    break
                time.sleep(1)
        except Exception as e:
            print(f"[!] workable {country}: {e}", file=sys.stderr)
    return list(jobs.values())
