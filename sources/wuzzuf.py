"""Wuzzuf direct (Egypt + Saudi), from its public category pages (/a/<Slug>-Jobs-in-Egypt).

robots.txt allows these pages (it blocks only query URLs like /search/jobs/?q=, which are also
Cloudflare-protected). Each page embeds `Wuzzuf.initialStoreState` JSON with the ~20 newest jobs:
title, company, exact postedAt (UTC), description, requirements, experience, applicant count.
Pages list newest first, so page 1 per category is enough for an hourly run.
Which pages: config.toml [wuzzuf] pages.
"""
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import USER_AGENT, make_job

STATE_START = re.compile(r"Wuzzuf\.initialStoreState\s*=\s*")


def _parse_posted_at(raw):
    """'MM/DD/YYYY HH:MM:SS' (UTC) -> aware datetime, or None."""
    try:
        return datetime.strptime(raw.strip(), "%m/%d/%Y %H:%M:%S").replace(tzinfo=timezone.utc)
    except (AttributeError, ValueError):
        return None


def _state(page):
    """The embedded JSON object. raw_decode reads exactly one object, whatever follows it."""
    m = STATE_START.search(page)
    if not m:
        return {}
    try:
        return json.JSONDecoder().raw_decode(page, m.end())[0]
    except ValueError:
        return {}


def _location(attrs):
    loc = attrs.get("location") or {}
    place = ", ".join(p.strip() for p in ((loc.get(k) or {}).get("name") for k in ("area", "city", "country")) if p and p.strip())
    workplace = (attrs.get("workplaceArrangement") or {}).get("displayedName")
    return f"{place} ({workplace})" if workplace else place


def _extract_jobs_from_html(page):
    entities = _state(page).get("entities") or {}
    jobs_col = (entities.get("job") or {}).get("collection") or {}
    companies = (entities.get("company") or {}).get("collection") or {}
    jobs = []
    for j in jobs_col.values():
        attrs = j.get("attributes") or {}
        if (attrs.get("status") or "active") != "active":  # missing/null status = active
            continue
        uri = attrs.get("uri") or (f"jobs/p/{attrs['slug']}" if attrs.get("slug") else "")
        if not uri:
            continue
        company_id = (((j.get("relationships") or {}).get("company") or {}).get("data") or {}).get("id")
        company = ((companies.get(company_id) or {}).get("attributes") or {}).get("name") \
            or ("Confidential Company" if attrs.get("hideCompany") else "")
        keywords = " ".join(k.get("name", "") for k in attrs.get("keywords") or [] if isinstance(k, dict))
        exp = attrs.get("workExperienceYears") or {}
        exp_text = f"{exp['min']}-{exp.get('max')} years" if exp.get("min") is not None else ""
        job = make_job(attrs.get("title"), company, _location(attrs),
                       uri if uri.startswith("http") else f"https://wuzzuf.net/{uri.lstrip('/')}",
                       _parse_posted_at(attrs.get("postedAt")), "wuzzuf",
                       text=(attrs.get("description"), attrs.get("requirements"), keywords, exp_text))
        applied = (attrs.get("insights") or {}).get("applied")
        if applied:
            job["note"] = f"👥 {applied} متقدم"  # how crowded it is; shown in the Telegram message
        jobs.append(job)
    return jobs


def fetch(section, cfg):
    # cookies: /saudi/... sets one and redirects; without a jar it loops on 302
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener.addheaders = [("User-Agent", USER_AGENT)]
    jobs = {}
    for i, url in enumerate(section.get("pages", [])):
        if i:
            time.sleep(section.get("pause_seconds", 3))  # 0.6s got 429 on half the pages
        try:
            with opener.open(url, timeout=20) as resp:
                page = resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Wuzzuf says slow down: stop now, the rest waits for the next run
                print(f"[!] wuzzuf: 429 at {url}, skipping the remaining pages this run", file=sys.stderr)
                break
            print(f"[!] wuzzuf {url}: {e}", file=sys.stderr)
            continue
        except Exception as e:  # one page down must not lose the others
            print(f"[!] wuzzuf {url}: {e}", file=sys.stderr)
            continue
        for job in _extract_jobs_from_html(page):
            jobs[job["url"]] = job
    return list(jobs.values())
