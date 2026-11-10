"""Job-alert emails in Gmail (IMAP, read-only): Wuzzuf + LinkedIn.

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
from datetime import date, timedelta
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


PARSERS = {  # IMAP "FROM" search text -> parser
    "wuzzuf.net": parse_wuzzuf,
    "jobalerts-noreply@linkedin.com": parse_linkedin,
}


def message_texts(msg):
    """Every text version of the email (plain, and HTML turned into lines)."""
    texts = []
    for part in msg.walk():
        kind = part.get_content_type()
        if kind in ("text/plain", "text/html"):
            body = part.get_content()
            texts.append(html_to_text(body) if kind == "text/html" else body)
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
                for text in message_texts(msg):  # plain + html both parsed; same job kept once
                    for job in parse(text, received):
                        jobs.setdefault(job["url"], job)
    return list(jobs.values())
