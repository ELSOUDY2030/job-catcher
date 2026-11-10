"""Match score 0-10: how well a job fits the CV. Weights live in config.toml [score].

A keyword in the title counts double; in the description once (capped). Negative weights push
down jobs that need what the CV does not have. Asking for many more years than you have costs points.

ponytail: keyword weights, not an LLM. Upgrade path: send title + description to an LLM
when keywords mislead (e.g. "AI" in a marketing title).
"""
import re

from sources import words_regex

YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs)\b", re.IGNORECASE)


def rules(cfg):
    """[(regex, weight), ...] compiled once per run."""
    return [(words_regex([word]), weight) for word, weight in cfg.get("score", {}).get("keywords", {}).items()]


def years_asked(text):
    """Smallest 'N years' in the text (descriptions often list several), or None."""
    found = [int(n) for n in YEARS.findall(text) if 0 < int(n) < 30]
    return min(found) if found else None


def score(job, compiled, cfg):
    s = cfg.get("score", {})
    title, text = job["title"], job.get("text", "")
    in_title = in_text = 0
    for regex, weight in compiled:
        if regex.search(title):
            in_title += 2 * weight
        elif text and regex.search(text):
            in_text += weight
    # long descriptions mention everything, so they may add only a little (bad words still count in full)
    raw = in_title + min(in_text, s.get("description_cap", 4))
    years = years_asked(f"{title} {text}")
    if years is not None and years > s.get("my_years", 5) + 1:
        raw -= s.get("too_many_years_penalty", 4)
    return max(0, min(10, round(raw * 10 / s.get("full_marks", 10))))
