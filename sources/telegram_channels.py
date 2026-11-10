"""Public Telegram channels, read from their web preview (t.me/s/<channel>), no login.

Posts are free text, so the "title" is the first line that matches the include filter
(or the first line, which the filter then drops). Only the ~20 latest posts are visible.
"""
import html
import re
import sys
import urllib.request

from . import USER_AGENT, make_job, words_regex

TEXT = re.compile(r'tgme_widget_message_text[^>]*>(.*?)</div>', re.S)
TIME = re.compile(r'<time datetime="([^"]+)"')


def _text(fragment):
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def parse_channel(page, channel, include, must_have=None):
    jobs = []
    for chunk in page.split('data-post="')[1:]:  # one chunk per post, so a text-less post can't borrow the next one's text
        post = chunk.split('"', 1)[0]
        text, posted = TEXT.search(chunk), TIME.search(chunk)
        if not text:
            continue
        lines = [line.strip(" -•|*") for line in _text(text.group(1)).splitlines() if line.strip(" -•|*")]
        if not lines or (must_have and not must_have.search(" ".join(lines))):
            continue  # empty, or news/course post rather than a job
        title = next((line for line in lines if include and include.search(line)), lines[0])
        jobs.append(make_job(title[:150], f"@{channel}", "", f"https://t.me/{post}",
                             posted.group(1) if posted else None, "telegram", text="\n".join(lines)))
    return jobs


def fetch(section, cfg):
    include = words_regex(cfg.get("filter", {}).get("include"))
    must_have = words_regex(section.get("must_have"))
    jobs = []
    for channel in section.get("channels", []):
        req = urllib.request.Request(f"https://t.me/s/{channel}", headers={"User-Agent": USER_AGENT})
        try:
            page = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        except Exception as e:
            print(f"[!] telegram @{channel}: {e}", file=sys.stderr)
            continue
        jobs += parse_channel(page, channel, include, must_have)
    return jobs
