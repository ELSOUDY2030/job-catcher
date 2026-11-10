"""Company career pages, read from their ATS's public job-board API.

Which companies: config.toml [ats]. Jobs straight from the company, often before LinkedIn.
BambooHR and Pinpoint give no posting date, so their jobs count as new the first time we see them.
"""
import sys

from . import get_json, make_job, place_regex


def _join(*parts):
    return " · ".join(p for p in parts if p)


def _greenhouse(slug):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    return [make_job(j.get("title"), j.get("company_name") or slug, (j.get("location") or {}).get("name"),
                     j.get("absolute_url"), j.get("first_published") or j.get("updated_at"), "greenhouse",
                     text=j.get("content"))
            for j in data.get("jobs", [])]


def _lever(slug):
    jobs = []
    for j in get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json"):
        created = j.get("createdAt")  # milliseconds
        jobs.append(make_job(j.get("text"), slug, _join((j.get("categories") or {}).get("location"), j.get("workplaceType")),
                             j.get("hostedUrl"), created / 1000 if created else None, "lever",
                             text=(j.get("descriptionPlain"), j.get("additionalPlain"))))
    return jobs


def _ashby(slug):
    jobs = []
    for j in get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").get("jobs", []):
        locs = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]
        jobs.append(make_job(j.get("title"), slug, _join(*locs, "Remote" if j.get("isRemote") else ""),
                             j.get("jobUrl"), j.get("publishedAt"), "ashby", text=j.get("descriptionPlain")))
    return jobs


def _smartrecruiters(slug):
    jobs = []
    for j in get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100").get("content", []):
        loc = j.get("location") or {}
        where = loc.get("fullLocation") or _join(loc.get("city"), loc.get("region"), loc.get("country"))
        company = (j.get("company") or {}).get("name") or slug
        jobs.append(make_job(j.get("name"), company, _join(where, "Remote" if loc.get("remote") else ""),
                             f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", j.get("releasedDate"), "smartrecruiters"))
    return jobs


def _recruitee(slug):
    return [make_job(o.get("title"), o.get("company_name") or slug,
                     _join(o.get("location"), o.get("country"), "Remote" if o.get("remote") else ""),
                     o.get("careers_url"), o.get("published_at"), "recruitee",
                     text=(o.get("description"), o.get("requirements")))
            for o in get_json(f"https://{slug}.recruitee.com/api/offers/").get("offers", [])]


def _breezy(slug):
    jobs = []
    for j in get_json(f"https://{slug}.breezy.hr/json"):
        loc = j.get("location") or {}
        jobs.append(make_job(j.get("name"), slug, _join(loc.get("name"), "Remote" if loc.get("is_remote") else ""),
                             j.get("url"), j.get("published_date"), "breezy"))
    return jobs


def _bamboohr(slug):
    jobs = []
    for j in get_json(f"https://{slug}.bamboohr.com/careers/list").get("result", []):
        loc = j.get("location") or {}
        jobs.append(make_job(j.get("jobOpeningName"), slug, _join(loc.get("city"), loc.get("state"), "Remote" if j.get("isRemote") else ""),
                             f"https://{slug}.bamboohr.com/careers/{j.get('id')}", None, "bamboohr"))
    return jobs


def _pinpoint(slug):
    jobs = []
    for j in get_json(f"https://{slug}.pinpointhq.com/postings.json").get("data", []):
        loc = j.get("location") or {}
        jobs.append(make_job(j.get("title"), slug, _join(loc.get("city"), loc.get("name"), j.get("workplace_type")),
                             j.get("url"), None, "pinpoint",
                             text=(j.get("description"), j.get("key_responsibilities"), j.get("skills_knowledge_expertise"))))
    return jobs


BOARDS = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "smartrecruiters": _smartrecruiters,
    "recruitee": _recruitee,
    "breezy": _breezy,
    "bamboohr": _bamboohr,
    "pinpoint": _pinpoint,
}


def location_ok(location, places):
    """Company boards list jobs worldwide: keep only [regions] places (empty location = drop)."""
    return bool(places and location and places.search(location))


def fetch(section, cfg):
    places = place_regex(cfg)
    jobs = []
    for ats, read in BOARDS.items():
        for slug in section.get(ats, []):
            try:
                found = read(slug)
            except Exception as e:  # one company's board down must not lose the others
                print(f"[!] ats {ats}/{slug}: {e}", file=sys.stderr)
                continue
            jobs += [j for j in found if location_ok(j["location"], places)]
    return jobs
