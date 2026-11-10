"""Shared helpers. Every source returns a list of jobs shaped like make_job()."""
import html
import json
import re
import urllib.request
from datetime import date, datetime, time, timezone

USER_AGENT = "job-catcher/0.1 (personal job alerts)"


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def words_regex(words):
    """["ai", "machine learning"] -> one case-insensitive whole-word regex (None if empty)."""
    if not words:
        return None
    # lookarounds, not \b: \b fails at the edge of ".net" or "node.js"
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.IGNORECASE)


GROUPS = ("always", "gulf", "arab", "europe")  # config.toml [regions]


def countries_this_run(cfg, now=None):
    """[regions] always (every run) + the next `per_run` countries, rotating by half-hour slot.

    ponytail: rotation keeps each run short and under LinkedIn's 429 limit; every country
    comes up every len(rest)/per_run runs (~4h at one run per 30 min). More per_run = faster, slower runs.
    """
    regions = cfg.get("regions", {})
    always = regions.get("always", [])
    rest = [c for g in GROUPS[1:] for c in regions.get(g, []) if c not in always]
    if regions.get("per_run", 4) <= 0:  # 0 = every country, every run
        return always + rest
    n = min(regions.get("per_run", 4), len(rest))
    now = now or datetime.now(timezone.utc)
    slot = (now.hour * 60 + now.minute) // 30  # 0..47: two runs in one hour get different countries
    start = (slot * n) % len(rest) if rest else 0
    return always + [rest[(start + i) % len(rest)] for i in range(n)]


def place_regex(cfg):
    """Matches any [regions] country or place word (cities, 'EMEA', 'worldwide'...) in a location."""
    regions = cfg.get("regions", {})
    return words_regex([c for g in GROUPS for c in regions.get(g, [])] + regions.get("place_words", []))


def clean(value):
    """Text or "". pandas gives NaN for empty cells, and NaN != NaN."""
    return "" if value is None or value != value else str(value).strip()


def parse_time(value):
    """Epoch, ISO string, datetime or date -> aware UTC datetime (None if unknown).

    A bare date becomes the END of that day, so a date-only job is never dropped too early.
    """
    if value is None or value != value or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        return datetime.combine(value, time.max, timezone.utc)
    else:
        try:  # also "2026-10-12 15:06:22 UTC" (Recruitee)
            dt = datetime.fromisoformat(str(value).strip().removesuffix(" UTC").replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def plain(*parts, limit=3000):
    """HTML/text pieces -> one short plain string (for scoring only, never stored)."""
    text = " ".join(clean(p) for p in parts if clean(p))
    text = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(text)))  # twice: Greenhouse double-escapes
    return " ".join(text.split())[:limit]


def make_job(title, company, location, url, posted, source, text=""):
    return {
        "title": html.unescape(clean(title)),  # LinkedIn sends "&#8211;" etc. in some titles
        "company": html.unescape(clean(company)),
        "location": clean(location),
        "url": clean(url),
        "posted": parse_time(posted),
        "source": source,
        # description when the source gives it for free (str or tuple of parts); used by the score
        "text": plain(*text) if isinstance(text, tuple) else plain(text),
    }
