"""Remote job boards with official public APIs.

Their terms ask us to keep the original job URL and name the board as the source;
every message does both.
"""
import urllib.parse

from . import get_json, make_job, place_regex


def location_ok(location, cfg):
    """Keep remote jobs open to [regions] places: empty location = anywhere."""
    places = place_regex(cfg)
    return not location or not places or bool(places.search(location))


def _keep(jobs, cfg):
    return [j for j in jobs if location_ok(j["location"], cfg)]


def remotive(section, cfg):
    # One GET for all jobs: Remotive asks for max 4 requests/day.
    data = get_json("https://remotive.com/api/remote-jobs")
    return _keep([make_job(j.get("title"), j.get("company_name"), j.get("candidate_required_location"),
                           j.get("url"), j.get("publication_date"), "remotive", text=j.get("description"))
                  for j in data.get("jobs", [])], cfg)


def remoteok(section, cfg):
    data = get_json("https://remoteok.com/api")[1:]  # item 0 is the legal notice
    return _keep([make_job(j.get("position"), j.get("company"), j.get("location"),
                           j.get("url"), j.get("date"), "remote ok", text=j.get("description"))
                  for j in data], cfg)


def himalayas(section, cfg):
    jobs = []
    for term in section.get("terms", []):
        data = get_json(f"https://himalayas.app/jobs/api/search?q={urllib.parse.quote(term)}&limit=20")
        jobs += [make_job(j.get("title"), j.get("companyName"), ", ".join(j.get("locationRestrictions") or []),
                          j.get("guid") or j.get("applicationLink"), j.get("pubDate"), "himalayas",
                          text=(j.get("excerpt"), j.get("description")))
                 for j in data.get("jobs", [])]
    return _keep(jobs, cfg)


def jobicy(section, cfg):
    data = get_json("https://jobicy.com/api/v2/remote-jobs?count=100")
    return _keep([make_job(j.get("jobTitle"), j.get("companyName"), j.get("jobGeo"),
                           j.get("url"), j.get("pubDate"), "jobicy", text=(j.get("jobExcerpt"), j.get("jobDescription")))
                  for j in data.get("jobs", [])], cfg)


def arbeitnow(section, cfg):
    # Mostly on-site jobs in Germany, so no remote-location filter here.
    data = get_json("https://www.arbeitnow.com/api/job-board-api")
    return [make_job(j.get("title"), j.get("company_name"), j.get("location"),
                     j.get("url"), j.get("created_at"), "arbeitnow")
            for j in data.get("data", [])]
