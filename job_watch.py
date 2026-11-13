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
import math
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
from sources import (ats, email_alerts, jobspy_boards, pc_feed, remote_boards, telegram_channels, workable,
                     words_regex, wuzzuf)

HERE = Path(__file__).parent
CONFIG_FILE = HERE / "config.toml"
SEEN_FILE = HERE / "seen_jobs.json"
STATE_FILE = HERE / "state.json"
KEEP_DAYS = 30  # forget a job this long after it was last listed anywhere

# config.toml section name -> fetch(section, cfg) -> list of jobs
SOURCES = {
    "jobspy": jobspy_boards.fetch,
    "wuzzuf": wuzzuf.fetch,        # runs_on = "pc": skipped here, arrives through pc_feed
    "bayt": jobspy_boards.fetch_bayt,  # runs_on = "pc" too
    "pc_feed": pc_feed.fetch,
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


def search_hours(last, now, cap):
    """LinkedIn/Indeed only ask for jobs since the last run (+15 min for the run itself), in whole hours
    (JobSpy's unit, min 1). A missed run makes the next window bigger, so nothing falls through.
    ponytail: only for JobSpy; other boards give a date without a time, so they keep the 24 h filter."""
    if not last:
        return cap
    gone = (now - datetime.fromisoformat(last)).total_seconds() / 3600 + 0.25
    return max(1, min(cap, math.ceil(gone)))


def collect(cfg, now, ignore_schedule=False):
    """Run every due source at the same time (each talks to a different site), keep fresh jobs."""
    due = {name: fetch for name, fetch in SOURCES.items()
           if cfg.get(name, {}).get("enabled", True) and cfg.get(name, {}).get("runs_on") != "pc"
           and (ignore_schedule or is_due(cfg.get(name, {}), now))}
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


def make_record(key, job, score, now, auto_min_score=None):
    auto = auto_min_score is not None and score >= auto_min_score
    rec = {
        "id": short_id(key),
        "title": job["title"],
        "company": job["company"],
        "location": job["location"],
        "url": job["url"],
        "source": job["source"],
        "score": score,
        # new / queued (🤖 auto or 🚀) / applied / ignored / needs_you / blocked / skipped
        "status": "queued" if auto else "new",
        "auto": auto,
        "first_seen": now.isoformat(timespec="minutes"),
        "last_seen": now.date().isoformat(),
        "posted": job["posted"].isoformat(timespec="minutes") if job.get("posted") else None,  # PC: same-day cutoff
    }
    if auto and job.get("text"):  # the PC's CV maker screens it (Indeed pages can't be fetched); dropped when done
        rec["text"] = job["text"]
    return rec


def apply_pc_results(seen, pc, now=None):
    """Your PC reports what happened to queued jobs (applied / needs_you / blocked / skipped).

    status_at = when GitHub learned it (so the hourly summary never misses a result the PC
    reported late); done_at = when the PC did it.
    """
    by_id = {rec["id"]: rec for rec in seen.values()}
    changed = 0
    for job_id, result in (pc.get("results") or {}).items():
        rec = by_id.get(job_id)
        if rec and rec.get("status") == "queued":  # a later manual ✅/❌ from you always wins
            rec.update(status=result["status"], done_at=result.get("at", ""), note=result.get("note", ""),
                       proof=result.get("proof", ""),
                       status_at=now.isoformat(timespec="minutes") if now else result.get("at", ""))
            rec.pop("text", None)
            changed += 1
    return changed


def format_job(job, score=None, queued=False):
    e = lambda key: html.escape(job.get(key) or "")
    posted = f"  ⏱ {job['posted']:%Y-%m-%d}" if job.get("posted") else ""
    stars = f"⭐ {score}/10  " if score is not None else ""
    lines = [
        f"🆕 <b>{e('title')}</b>",
        f"🏢 {e('company')}  📍 {e('location')}",
        f"{stars}🌐 {e('source')}{posted}",
        e("note"),  # optional extra a source adds (Wuzzuf: applicant count)
        "🤖 هيتقدم عليها أوتوماتيك من جهازك" if queued else "",
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
    pc = pc_feed.load(cfg.get("pc_feed"))
    print(f"[i] pc: {apply_pc_results(seen, pc, now)} application results from your PC")

    js = cfg.setdefault("jobspy", {})
    js["query_hours"] = search_hours(state.get("last_search"), now, js.get("hours_old", cfg["hours_old"]))
    print(f"[i] LinkedIn/Indeed: jobs from the last {js['query_hours']} h")
    jobs = collect(cfg, now, ignore_schedule=dry_run)
    if not dry_run:
        state["last_search"] = now.isoformat(timespec="minutes")
    for job in jobs:  # still listed -> remember longer (undated jobs would come back after KEEP_DAYS)
        key = job_key(job)
        if key in seen:
            seen[key]["last_seen"] = today

    compiled = scoring.rules(cfg)
    min_score = cfg.get("score", {}).get("min_score", 0)
    ranked = sorted(((scoring.score(job, compiled, cfg), key, job) for key, job in select_new(jobs, cfg, seen).items()),
                    key=lambda item: -item[0])  # best match first
    low = [item for item in ranked if item[0] < min_score]
    ranked = [item for item in ranked if item[0] >= min_score]
    summary_mode = cfg.get("telegram", {}).get("mode", "summary") == "summary"
    auto_min = cfg.get("apply", {}).get("auto_min_score")

    if summary_mode:
        # no message per job: everything is recorded now and reported in the hourly summary
        for score, key, job in low:  # remembered small, so a weak job is counted once and never again
            seen[key] = {"id": short_id(key), "status": "low", "score": score,
                         "first_seen": now.isoformat(timespec="minutes"), "last_seen": today}
        for score, key, job in ranked:
            seen[key] = make_record(key, job, score, now, auto_min)
        print(f"[i] {len(ranked)} new jobs recorded (+{len(low)} below min_score), no per-job messages")
    else:
        cap = cfg.get("max_messages_per_run", 40)  # best first; the rest stay unsent and come back next run
        print(f"[i] {len(ranked)} new jobs" + (f", sending the best {cap} now" if len(ranked) > cap else ""))
        for score, key, job in ranked[:cap]:
            record = make_record(key, job, score, now, auto_min)
            if dry_run:
                print(format_job(job, score, queued=record["auto"]), end="\n\n")
                seen[key] = record
                continue
            try:
                record["msg"] = telegram_bot.send(token, chat_id, format_job(job, score, queued=record["auto"]),
                                                  telegram_bot.buttons(record["id"], record["status"]))
            except Exception as e:  # not marked seen, so it is retried next run
                print(f"[!] send failed for {job['url']}: {e}", file=sys.stderr)
                continue
            seen[key] = record
            time.sleep(1.1)  # Telegram allows ~1 message/second per chat

    if summary_mode:
        minutes = cfg.get("telegram", {}).get("summary_minutes", 60)
        since = state.get("summary_from") or (now - timedelta(minutes=minutes)).isoformat(timespec="minutes")
        text, markup = telegram_bot.summary(seen, since, now, cfg, pc)
        if dry_run:
            print("----- summary preview -----\n" + text)
            return
        # runs are every 30 min (or late): send once at least `minutes` - 5 have passed
        if now - datetime.fromisoformat(since) >= timedelta(minutes=minutes - 5):
            try:
                telegram_bot.send(token, chat_id, text, markup)
                state["summary_from"] = now.isoformat(timespec="minutes")
            except Exception as e:  # window not moved: the next run sends it, nothing lost
                print(f"[!] summary: {e}", file=sys.stderr)
    else:
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
