"""LinkedIn + Indeed (GitHub) and Bayt (your PC) through the JobSpy library, per [regions] country.

One OR query per country for LinkedIn + Indeed (both understand OR). Bayt reads the query as a
URL slug, so it gets one plain term per call, and only Arab/Gulf countries (that's where Bayt is).
Bayt answers 403 to GitHub's servers, so `fetch_bayt` runs on your PC (pc/pc_runner.py).
"""
import re
import sys
import time

from . import clean, countries_this_run, make_job

INDEED_NAMES = {"united kingdom": "uk", "czechia": "czech republic", "turkey": "türkiye"}  # JobSpy's spelling
# LinkedIn reads "Lebanon" as Lebanon, PA (and "Jordan" can be Jordan, MN). We never search the US,
# so a US location always means LinkedIn picked the wrong place.
WRONG_PLACE = re.compile(r"(?<!\w)(USA|United States)(?!\w)", re.IGNORECASE)


def wrong_place(location):
    return bool(location and WRONG_PLACE.search(location))


def _search(sites, term, country, indeed_name, section, cfg):
    from jobspy import scrape_jobs  # heavy import (pandas), only when a search runs

    try:
        df = scrape_jobs(
            site_name=sites,
            search_term=term,
            location=country,
            country_indeed=indeed_name or "usa",  # must be valid even when Indeed isn't searched
            # query_hours: only the time since the last run (job_watch.search_hours); the 24 h stay for the filter
            hours_old=section.get("query_hours") or section.get("hours_old", cfg["hours_old"]),
            results_wanted=section.get("results_wanted", 25),
            verbose=0,
        )
    except Exception as e:  # one bad search must not lose the others
        print(f"[!] jobspy {sites} {country}: {e}", file=sys.stderr)
        return []
    return [make_job(row.get("title"), row.get("company"), row.get("location"), row.get("job_url"),
                     row.get("date_posted"), clean(row.get("site")), text=row.get("description"))
            for row in df.to_dict("records") if not wrong_place(clean(row.get("location")))]


def fetch(section, cfg):
    """LinkedIn + Indeed, every country of this run."""
    from jobspy.model import Country

    indeed_countries = {c.value for c in Country}
    query = " OR ".join(f'"{t}"' if " " in t else t for t in section["terms"])
    jobs = []
    for i, country in enumerate(countries_this_run(cfg)):
        if i:
            time.sleep(10)  # ponytail: fixed pause against LinkedIn 429; proxies if it still blocks
        indeed_name = INDEED_NAMES.get(country.lower(), country.lower())
        has_indeed = indeed_name in indeed_countries
        sites = [s for s in section["sites"] if s == "linkedin" or (s == "indeed" and has_indeed)]
        if sites:
            jobs += _search(sites, query, country, indeed_name if has_indeed else None, section, cfg)
    return jobs


def fetch_bayt(section, cfg):
    """Bayt: Egypt + Gulf + Arab countries, one plain term per call (runs on your PC)."""
    regions = cfg.get("regions", {})
    countries = regions.get("always", []) + regions.get("gulf", []) + regions.get("arab", [])
    return [job for country in countries for term in section.get("terms", [])
            for job in _search(["bayt"], term, country, None, section, cfg)]
