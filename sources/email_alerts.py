"""Job-alert emails in Gmail (IMAP, read-only): Wuzzuf, LinkedIn, NaukriGulf, GulfTalent.

Wuzzuf blocks scripts (Cloudflare), but its alert emails reach your inbox, so we read those.
Needs GMAIL_ADDRESS + GMAIL_APP_PASSWORD (a Gmail App Password, not your real password).
Without them this source is skipped. Mailbox is opened read-only: nothing is marked read.

ponytail: one small parser per sender, built from real emails (2026-11-08).
Add Glassdoor / NaukriGulf / Bayt parsers when we have a sample of each.
"""
import email
import email.policy
import html
import imaplib
import os
import re
import urllib.parse
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

from . import make_job

LINK_LINE = re.compile(r"^\[([^\]]+)\]\((\S+)\)$")
AGO = re.compile(r"(\d+)\s*(minute|hour|day|week|month)s?\s+ago", re.I)
DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30}


def html_to_text(page):
    """HTML -> lines, with links kept as [text](url) (the shape the parsers expect)."""
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", page)
    page = re.sub(r'(?is)<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>',
                  lambda m: f"\n[{' '.join(re.sub(r'<[^>]+>', ' ', m[2]).split())}]({m[1]})\n", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|td|li|h\d|table)>", "\n", page)
    return html.unescape(re.sub(r"<[^>]+>", "", page))


def _lines(text):
    return [line.strip(" *\t") for line in text.splitlines() if line.strip(" *\t")]


def _posted(text, received):
    m = AGO.search(text)
    return received - timedelta(days=int(m[1]) * DAYS[m[2].lower()]) if m else received


def parse_wuzzuf(text, received):
    """[Title](tracking-link) then 'Company - City, Egypt - Posted 3 days ago'."""
    jobs, lines = [], _lines(text)
    for i, line in enumerate(lines[:-1]):
        link = LINK_LINE.match(line)
        if not link:
            continue
        real = re.search(r"https://wuzzuf\.net/(?:jobs/p|internship)/[A-Za-z0-9]+-[^?&\s/)]+",
                         urllib.parse.unquote(urllib.parse.unquote(link[2])))
        details = lines[i + 1]
        if not real or " - Posted" not in details:  # also skips "you just applied to [X]" links
            continue
        parts = [p.strip(" *") for p in details.split(" - ")]
        company, location = " - ".join(parts[:-2]), parts[-2] if len(parts) >= 3 else ""
        jobs.append(make_job(link[1], company, location, real[0], _posted(details, received), "wuzzuf"))
    return jobs


LINKEDIN_FLUFF = re.compile(
    r"actively hiring|apply with resume|easy apply|early applicant|actively recruiting|promoted|"
    r"connection|alumni|^-{3,}$|jobs in |new jobs|job alert|see all jobs", re.I)


def parse_linkedin(text, received):
    """Text part: Title / Company / Location / (fluff) / 'View job: <url>'."""
    jobs, block = [], []
    for line in _lines(text):
        view = re.search(r"View job:\s*https://www\.linkedin\.com/comm/jobs/view/(\d+)", line)
        if view:
            if len(block) >= 3:
                title, company, location = block[-3:]
                jobs.append(make_job(title, company, location, f"https://www.linkedin.com/jobs/view/{view[1]}",
                                     received, "linkedin alert"))
            block = []
        elif "http" in line:
            block = []
        elif not LINKEDIN_FLUFF.search(line):
            block.append(line)
    return jobs


def _cells(inner_html):
    """The text pieces of one job card (each <td>/<p> its own piece)."""
    return [" ".join(html.unescape(t).split()) for t in re.split(r"<[^>]+>", inner_html)
            if " ".join(html.unescape(t).split())]


def _anchors(page):
    """(real href, card html) for every link. Nested links end a card early: that card is skipped."""
    return [(html.unescape(m[1]), m[2]) for m in re.finditer(r'(?is)<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', page)]


NG_URL = re.compile(r"https://www\.naukrigulf\.com/[a-z0-9-]+-jid-\d+")
NG_AGE = re.compile(r"^(\d+)([hd])$")


def _ng_posted(cell, received):
    """'2d' / '5h' / '1 Nov' / 'Today' -> datetime."""
    m = NG_AGE.match(cell)
    if m:
        return received - timedelta(days=int(m[1]) if m[2] == "d" else int(m[1]) / 24)
    try:
        day = datetime.strptime(f"{cell} {received.year}", "%d %b %Y").replace(tzinfo=received.tzinfo)
        return day if day <= received else day.replace(year=received.year - 1)
    except ValueError:
        return received


def parse_naukrigulf(page, received):
    """HTML: each job is one link around Title / Company / 'X - Y Years' / 'City - Country' / [Easy Apply] / age.
    The link is a tracking + AUTO-LOGIN redirect: only the job page URL inside it is kept."""
    jobs = []
    for href, inner in _anchors(page):
        real = NG_URL.search(urllib.parse.unquote(urllib.parse.unquote(href)))
        cells = _cells(inner)
        years = next((i for i, c in enumerate(cells) if re.search(r"\d+\s*-\s*\d+\s+Years?", c)), None)
        if not real or years is None or years < 2 or years + 1 >= len(cells):
            continue
        location = ", ".join(p.strip() for p in cells[years + 1].replace(" , ", "; ").split(" - "))
        jobs.append(make_job(cells[0], cells[1], location, real[0], _ng_posted(cells[-1], received), "naukrigulf"))
    return jobs


GT_URL = re.compile(r"https://www\.gulftalent\.com/[a-z-]+/jobs/[a-z0-9-]+-\d+")


def parse_gulftalent(page, received):
    """HTML: each job is one link around Title / Company / Location / 'N days ago' / [Easy Apply].
    The link carries a login token (jwt=...): only the bare job URL is kept."""
    jobs = []
    for href, inner in _anchors(page):
        real = GT_URL.match(href)
        cells = _cells(inner)
        age = next((i for i, c in enumerate(cells) if AGO.search(c)), None)
        if real and age is not None and age >= 3:
            jobs.append(make_job(cells[0], cells[1], cells[2], real[0], _posted(cells[age], received), "gulftalent"))
    return jobs


HTML_PARSERS = {  # these read the email's HTML itself (their cards have no line breaks once flattened)
    "naukrigulf.com": parse_naukrigulf,
    "jobs@gulftalent.com": parse_gulftalent,
}

PARSERS = {  # IMAP "FROM" search text -> parser
    "wuzzuf.net": parse_wuzzuf,
    "jobalerts-noreply@linkedin.com": parse_linkedin,
    **HTML_PARSERS,
}


def message_texts(msg, raw_html=False):
    """Every text version of the email (plain, and HTML turned into lines); raw_html: only the HTML parts, as is."""
    texts = []
    for part in msg.walk():
        kind = part.get_content_type()
        if kind == "text/html":
            texts.append(part.get_content() if raw_html else html_to_text(part.get_content()))
        elif kind == "text/plain" and not raw_html:
            texts.append(part.get_content())
    return texts


def fetch(section, cfg):
    address, password = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (address and password):
        print("[i] email_alerts: no GMAIL_ADDRESS / GMAIL_APP_PASSWORD, skipped")
        return []
    since = (date.today() - timedelta(days=section.get("days_back", 2))).strftime("%d-%b-%Y")
    jobs = {}
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(address, password)
        imap.select(section.get("folder", "INBOX"), readonly=True)  # read-only: never marks mail as read
        for sender in section.get("senders", list(PARSERS)):
            parse = PARSERS[sender]
            _, found = imap.search(None, "FROM", f'"{sender}"', "SINCE", since)
            for num in found[0].split():
                _, data = imap.fetch(num, "(BODY.PEEK[])")
                msg = email.message_from_bytes(data[0][1], policy=email.policy.default)
                received = parsedate_to_datetime(msg["Date"])
                for text in message_texts(msg, raw_html=sender in HTML_PARSERS):  # same job kept once
                    for job in parse(text, received):
                        jobs.setdefault(job["url"], job)
    return list(jobs.values())
