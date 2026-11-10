"""Job Catcher: collect new jobs from many sources, filter, dedupe, score, send to Telegram.

Settings live in config.toml. Memory:
  seen_jobs.json  every sent job (title, company, url, score, status) — also the export for job-hunter
  state.json      Telegram read position + last digest day
Without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID it is a dry run: prints, saves nothing,
and ignores every_hours so every enabled source runs once.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import scoring
import telegram_bot
from sources import (ats, email_alerts, jobspy_boards, remote_boards, telegram_channels, workable, words_regex,
                     wuzzuf)

HERE = Path(__file__).parent
CONFIG_FILE = HERE / "config.toml"
SEEN_FILE = HERE / "seen_jobs.json"
STATE_FILE = HERE / "state.json"
KEEP_DAYS = 30  # forget a job this long after it was last listed anywhere

# config.toml section name -> fetch(section, cfg) -> list of jobs
SOURCES = {
    "jobspy": jobspy_boards.fetch,
    "wuzzuf": wuzzuf.fetch,
    "workable": workable.fetch,
    "remotive": remote_boards.remotive,
    "remoteok": remote_boards.remoteok,
    "himalayas": remote_boards.himalayas,
    "jobicy": remote_boards.jobicy,
    "arbeitnow": remote_boards.arbeitnow,
    "ats": ats.fetch,
    "telegram_channels": telegram_channels.fetch,
    "email_alerts": email_alerts.fetch,
}


def job_key(job):
    """Same title + same company = same job, whichever site it came from.

    ponytail: company = its first word only, because sites spell it differently
    ("Gramian Consulting" vs "Gramian Consulting Group"). Ceiling: two companies starting
    with the same word posting the same title would merge; use fuzzy matching if that bites.
    """
    words = lambda s: re.findall(r"\w+", (s or "").lower())
    company = [w for w in words(job["company"]) if w != "the"][:1]
    return f"{' '.join(words(job['title']))}|{' '.join(company)}"


def short_id(key):
    """Fits Telegram's 64-byte button data."""
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def is_due(section, now):
    """every_hours = 6 -> once at 00, 06, 12, 18 UTC (first half-hour run only). Default: every run."""
    every = section.get("every_hours", 1)
    return every == 1 or (now.hour % every == 0 and now.minute < 30)


def collect(cfg, now, ignore_schedule=False):
    """Run every due source at the same time (each talks to a different site), keep fresh jobs."""
    due = {name: fetch for name, fetch in SOURCES.items()
           if cfg.get(name, {}).get("enabled", True) and (ignore_schedule or is_due(cfg.get(name, {}), now))}
    with ThreadPoolExecutor(max_workers=max(1, len(due))) as pool:
        futures = {name: pool.submit(fetch, cfg.get(name, {}), cfg) for name, fetch in due.items()}
    jobs = []
    for name, future in futures.items():  # SOURCES order, so "first source wins" in dedupe stays stable
        section = cfg.get(name, {})
        cutoff = now - timedelta(hours=section.get("hours_old", cfg["hours_old"]))
        try:
            found = future.result()
        except Exception as e:  # one broken source must not stop the others
            print(f"[!] {name}: {e}", file=sys.stderr)
            continue
        fresh = [j for j in found if j["posted"] is None or j["posted"] >= cutoff]
        print(f"[i] {name}: {len(found)} found, {len(fresh)} fresh")
        jobs += fresh
    return jobs


def select_new(jobs, cfg, seen):
    include = words_regex(cfg["filter"].get("include"))
    exclude = words_regex(cfg["filter"].get("exclude"))
    new = {}
    for job in jobs:
        title = job["title"]
        if not title or not job["url"]:
            continue
        if (include and not include.search(title)) or (exclude and exclude.search(title)):
            continue
        key = job_key(job)
        if key not in seen:
            new.setdefault(key, job)  # first source wins
    return new


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def prune(seen, today):
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    return {key: rec for key, rec in seen.items() if rec["last_seen"] >= cutoff}


def make_record(key, job, score, now):
    return {
        "id": short_id(key),
        "title": job["title"],
        "company": job["company"],
        "location": job["location"],
        "url": job["url"],
        "source": job["source"],
        "score": score,
        "status": "new",  # new / applied / ignored (Telegram buttons)
        "first_seen": now.isoformat(timespec="minutes"),
        "last_seen": now.date().isoformat(),
    }


def format_job(job, score=None):
    e = lambda key: html.escape(job.get(key) or "")
    posted = f"  ⏱ {job['posted']:%Y-%m-%d}" if job.get("posted") else ""
    stars = f"⭐ {score}/10  " if score is not None else ""
    lines = [
        f"🆕 <b>{e('title')}</b>",
        f"🏢 {e('company')}  📍 {e('location')}",
        f"{stars}🌐 {e('source')}{posted}",
        e("note"),  # optional extra a source adds (Wuzzuf: applicant count)
        job["url"],
    ]
    return "\n".join(line for line in lines if line.strip())


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    dry_run = not (token and chat_id)
    if dry_run:
        print("[i] No TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID: dry run, nothing sent or saved.")

    cfg = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    seen = prune(load_json(SEEN_FILE, {}), now.date())
    state = load_json(STATE_FILE, {})

    if not dry_run:
        try:
            telegram_bot.sync_presses(token, chat_id, state, seen, now)
        except Exception as e:
            print(f"[!] telegram buttons: {e}", file=sys.stderr)

    jobs = collect(cfg, now, ignore_schedule=dry_run)
    for job in jobs:  # still listed -> remember longer (undated jobs would come back after KEEP_DAYS)
        key = job_key(job)
        if key in seen:
            seen[key]["last_seen"] = today

    compiled = scoring.rules(cfg)
    min_score = cfg.get("score", {}).get("min_score", 0)
    ranked = sorted(((scoring.score(job, compiled, cfg), key, job) for key, job in select_new(jobs, cfg, seen).items()),
                    key=lambda item: -item[0])  # best match first
    ranked = [item for item in ranked if item[0] >= min_score]
    cap = cfg.get("max_messages_per_run", 40)  # best first; the rest stay unsent and come back next run
    print(f"[i] {len(ranked)} new jobs" + (f", sending the best {cap} now" if len(ranked) > cap else ""))
    ranked = ranked[:cap]

    for score, key, job in ranked:
        record = make_record(key, job, score, now)
        text = format_job(job, score)
        if dry_run:
            print(text, end="\n\n")
            seen[key] = record
            continue
        try:
            record["msg"] = telegram_bot.send(token, chat_id, text, telegram_bot.buttons(record["id"]))
        except Exception as e:  # not marked seen, so it is retried next run
            print(f"[!] send failed for {job['url']}: {e}", file=sys.stderr)
            continue
        seen[key] = record
        time.sleep(1.1)  # Telegram allows ~1 message/second per chat

    if dry_run:
        print("----- digest preview -----\n" + telegram_bot.digest(seen, now))
        return
    # first run at/after the hour (GitHub cron can be late or skip a run)
    if now.hour >= cfg.get("digest_hour_utc", 7) and state.get("last_digest") != today:
        try:
            telegram_bot.send(token, chat_id, telegram_bot.digest(seen, now))
            state["last_digest"] = today
        except Exception as e:
            print(f"[!] digest: {e}", file=sys.stderr)
    save_json(SEEN_FILE, seen)
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
