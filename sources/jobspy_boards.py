"""LinkedIn + Indeed + Bayt through the JobSpy library, for every [regions] country (rotating).

One OR query per country for LinkedIn + Indeed (both understand OR). Bayt reads the query as a
URL slug, so it gets one plain term per call, and only Arab/Gulf countries (that's where Bayt is).
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


def fetch(section, cfg):
    from jobspy import scrape_jobs  # heavy import (pandas), only when this source runs
    from jobspy.model import Country

    indeed_countries = {c.value for c in Country}
    regions = cfg.get("regions", {})
    bayt_countries = set(regions.get("always", []) + regions.get("gulf", []) + regions.get("arab", []))
    query = " OR ".join(f'"{t}"' if " " in t else t for t in section["terms"])
    sites = section["sites"]

    jobs = []
    for i, country in enumerate(countries_this_run(cfg)):
        if i:
            time.sleep(10)  # ponytail: fixed pause against LinkedIn 429; proxies if it still blocks
        indeed_name = INDEED_NAMES.get(country.lower(), country.lower())
        has_indeed = indeed_name in indeed_countries
        calls = [([s for s in sites if s == "linkedin" or (s == "indeed" and has_indeed)], query)]
        if "bayt" in sites and country in bayt_countries:
            calls += [(["bayt"], term) for term in section.get("bayt_terms", [])]
        for call_sites, term in calls:
            if not call_sites:
                continue
            try:
                df = scrape_jobs(
                    site_name=call_sites,
                    search_term=term,
                    location=country,
                    country_indeed=indeed_name if has_indeed else "usa",  # must be valid even when unused
                    hours_old=section.get("hours_old", cfg["hours_old"]),
                    results_wanted=section.get("results_wanted", 25),
                    verbose=0,
                )
            except Exception as e:  # one bad search must not lose the others
                print(f"[!] jobspy {call_sites} {country}: {e}", file=sys.stderr)
                continue
            for row in df.to_dict("records"):
                if wrong_place(clean(row.get("location"))):
                    continue
                jobs.append(make_job(row.get("title"), row.get("company"), row.get("location"),
                                     row.get("job_url"), row.get("date_posted"), clean(row.get("site")),
                                     text=row.get("description")))
    return jobs
