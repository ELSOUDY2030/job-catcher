"""Your PC's half of Job Catcher. Windows Task Scheduler runs it every 30 minutes (pc/install_task.ps1).

1. Scan Wuzzuf + Bayt (they answer 403 to GitHub's servers, not to a home PC).
2. Apply to queued jobs (🤖 score >= [apply] auto_min_score, or 🚀 tapped on Telegram) with
   Antigravity (agy) + the job-hunter skill, in two steps:
     fill (stops before Submit, writes receipts/<id>/answers.json)
     -> supervisor: audit.py rules, then `claude -p` for answers the rules can't verify
     -> submit (screenshot + receipts/<id>/result.json)   ... only if nothing is wrong
   Every result goes to Telegram: screenshot, the questions and the answers, the review.
3. Hand jobs + results to GitHub through the "pc" branch (AES-encrypted with .state_key).

Needs: Edge + the Playwright MCP server on port 8931 (start_playwright_mcp.bat), agy with the
allow-list from pc/agy-cli-settings.json, pc/.env.json (pc/setup_pc.py), pc/facts_rules.toml.
"""
import html
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent          # job-catcher/pc
ROOT = HERE.parent                              # job-catcher
CV_DIR = ROOT.parents[1]                        # D:\MY\cv\cloude_CV (skill, candidate_facts, CVs)
sys.path[:0] = [str(ROOT), str(HERE)]

import audit                                    # noqa: E402
import review                                   # noqa: E402
from prompts import FILL, SUBMIT                # noqa: E402
from sources import jobspy_boards, words_regex, wuzzuf  # noqa: E402

RECEIPTS = ROOT / "receipts"
LOG = RECEIPTS / "runner.log"
KEY_FILE = ROOT / ".state_key"
ENV_FILE = HERE / ".env.json"
FACTS = CV_DIR / "candidate_facts.md"
AGY = shutil.which("agy") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe")
OPENSSL = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
MCP_PORT = 8931
KEEP_DAYS = 30


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    RECEIPTS.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------- encrypted branches (same openssl format as the GitHub workflow) ----------

def crypt(data, decrypt=False):
    # key via env, not "file:": openssl can't open paths with Arabic letters (مشروع) on Windows
    cmd = [OPENSSL, "enc", "-aes-256-cbc", "-pbkdf2", "-pass", "env:STATE_KEY"] + (["-d"] if decrypt else ["-salt"])
    env = {**os.environ, "STATE_KEY": KEY_FILE.read_text(encoding="utf-8").strip()}
    return subprocess.run(cmd, input=data, capture_output=True, check=True, env=env).stdout


def git(*args, cwd=ROOT, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=check)


def branch_file(branch, name):
    """Decrypted bytes of <name> on origin/<branch>, or None if the branch doesn't exist yet."""
    if git("fetch", "--quiet", "--depth", "1", "origin", branch, check=False).returncode:
        return None
    return crypt(git("show", f"FETCH_HEAD:{name}").stdout, decrypt=True)


def load_seen():
    data = branch_file("state", "state.enc")
    if not data:
        return {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return json.load(tar.extractfile("seen_jobs.json"))


def load_pc():
    data = branch_file("pc", "pc.enc")
    return json.loads(data) if data else {}


def save_pc(pc):
    """One commit, force-pushed: the "pc" branch never grows a history."""
    url = git("remote", "get-url", "origin").stdout.decode().strip()
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "pc.enc").write_bytes(crypt(json.dumps(pc, ensure_ascii=False).encode()))
        git("init", "--quiet", "-b", "pc", cwd=tmp)
        git("add", "pc.enc", cwd=tmp)
        git("commit", "--quiet", "-m", "pc report [skip ci]", cwd=tmp)
        git("push", "--quiet", "--force", url, "pc", cwd=tmp)


# ---------- Telegram (send only: GitHub is the one reading button presses) ----------

def telegram(method, env, fields, photo=None):
    url = f"https://api.telegram.org/bot{env['token']}/{method}"
    if photo is None:
        req = urllib.request.Request(url, json.dumps(fields).encode(), {"Content-Type": "application/json"})
    else:  # multipart for the screenshot
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in fields.items()]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="proof.png"\r\n'
                     f'Content-Type: image/png\r\n\r\n'.encode() + Path(photo).read_bytes() + b"\r\n")
        req = urllib.request.Request(url, b"".join(parts) + f"--{boundary}--\r\n".encode(),
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    urllib.request.urlopen(req, timeout=60).read()


def notify(env, text, photo=None):
    try:
        if photo:
            telegram("sendPhoto", env, {"chat_id": env["chat_id"], "caption": text[:1000], "parse_mode": "HTML"}, photo)
        else:
            telegram("sendMessage", env, {"chat_id": env["chat_id"], "text": text[:4000], "parse_mode": "HTML",
                                          "disable_web_page_preview": True})
    except Exception as e:  # a lost message must not lose the result
        log(f"[!] telegram: {e}")


# ---------- 1. sources GitHub can't reach ----------

def scan(cfg):
    jobs = []
    for name, fetch in (("wuzzuf", wuzzuf.fetch), ("bayt", jobspy_boards.fetch_bayt)):
        section = cfg.get(name, {})
        if not section.get("enabled", True):
            continue
        try:
            found = fetch(section, cfg)
        except Exception as e:
            log(f"[!] {name}: {e}")
            continue
        log(f"[i] {name}: {len(found)} jobs")
        jobs += [{**j, "posted": j["posted"].isoformat() if j.get("posted") else None} for j in found]
    return jobs


# ---------- 2. apply ----------

def ready_to_apply():
    problems = []
    if not Path(AGY).exists():
        problems.append("Antigravity CLI (agy) not found")
    if not audit.RULES_FILE.exists():
        problems.append("pc/facts_rules.toml missing")
    try:
        socket.create_connection(("localhost", MCP_PORT), timeout=3).close()
    except OSError:
        problems.append(f"Playwright MCP not running on port {MCP_PORT} (start_playwright_mcp.bat + Edge)")
    return problems


def agy(prompt, log_file, continue_=False, minutes=20):
    cmd = [AGY] + (["--continue"] if continue_ else []) + [
        "--print-timeout", f"{minutes}m", "--log-file", str(log_file), "-p", prompt]
    subprocess.run(cmd, cwd=CV_DIR, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=(minutes + 5) * 60)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError):
        return None


def find_shot(job_id, since):
    """The Playwright MCP saves screenshots in its output folder (default: temp)."""
    roots = [RECEIPTS / "_shots", Path(tempfile.gettempdir()) / "playwright-mcp-output"]
    shots = [p for r in roots if r.exists() for p in r.rglob(f"{job_id}*.png") if p.stat().st_mtime >= since]
    return max(shots, key=lambda p: p.stat().st_mtime) if shots else None


def qa_text(questions, limit=15):
    lines = [f"• {html.escape(str(x.get('q', ''))[:80])}: <b>{html.escape(str(x.get('a', ''))[:80])}</b>"
             for x in questions[:limit]]
    if len(questions) > limit:
        lines.append(f"… +{len(questions) - limit} (receipts/…/answers.json)")
    return "\n".join(lines)


def finish(rec, status, note, env, rdir, answers=None, shot=None, review_note=""):
    icon = {"applied": "✅ اتقدّم", "needs_you": "✋ محتاجك", "blocked": "⛔ اتوقف قبل الإرسال",
            "skipped": "⏭ اتساب"}[status]
    head = (f"{icon}: <b>{html.escape(rec['title'])}</b>\n🏢 {html.escape(rec['company'])} · ⭐ {rec['score']}/10\n"
            f"{html.escape(note or '')[:600]}\n{rec['url']}")
    body = qa_text((answers or {}).get("questions", []))
    if shot:
        shutil.copy(shot, rdir / "confirmation.png")
        notify(env, head, photo=rdir / "confirmation.png")
        if body:
            notify(env, f"📝 <b>الأسئلة والإجابات</b>\n{body}\n{review_note}")
    else:
        notify(env, "\n\n".join(p for p in (head, body, review_note) if p))
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="minutes"), "status": status, "note": (note or "")[:300]}
    with (RECEIPTS / "index.jsonl").open("a", encoding="utf-8") as f:  # one line per job, for a quick review later
        f.write(json.dumps({"id": rec["id"], "title": rec["title"], "company": rec["company"], "url": rec["url"],
                            **entry}, ensure_ascii=False) + "\n")
    log(f"[{status}] {rec['title']} | {rec['company']} | {note}")
    return entry


def apply_one(rec, cfg, env, rules):
    rdir = RECEIPTS / rec["id"]
    rdir.mkdir(parents=True, exist_ok=True)
    answers_file, result_file = rdir / "answers.json", rdir / "result.json"
    for f in (answers_file, result_file):
        f.unlink(missing_ok=True)
    started = time.time()

    agy(FILL.format(cv_dir=CV_DIR, title=rec["title"], company=rec["company"], location=rec["location"],
                    score=rec["score"], url=rec["url"], answers_file=answers_file), rdir / "agy_fill.log")
    answers = read_json(answers_file)
    if not answers:
        return finish(rec, "needs_you", "Antigravity didn't finish filling the form (no receipt).", env, rdir)
    if answers.get("status") != "ready":
        status = "skipped" if answers.get("status") == "skipped" else "needs_you"
        return finish(rec, status, answers.get("reason", ""), env, rdir, answers)

    # supervisor: rules first, Claude only for what the rules can't verify
    verdict = audit.check(answers.get("questions", []), rules)
    review_note = f"🔎 مراجعة: {verdict['ok']} إجابة مطابقة للقواعد"
    if verdict["unsure"]:
        if cfg.get("apply", {}).get("claude_review", True):
            try:
                verdict["problems"] += review.review(rec, verdict["unsure"], FACTS)
                review_note += f"، و{len(verdict['unsure'])} راجعها Claude"
            except Exception as e:
                verdict["problems"].append({"q": "-", "a": "-", "why": f"Claude review failed: {e}"})
        else:
            review_note += f"، و{len(verdict['unsure'])} من غير مراجعة"
    (rdir / "audit.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
    if verdict["problems"]:
        why = "\n".join(f"• {p['q'][:70]} → {p['a'][:50]}: {p['why']}" for p in verdict["problems"][:6])
        return finish(rec, "blocked", f"المراجعة لقت مشكلة، فماتبعتش:\n{why}", env, rdir, answers, review_note=review_note)

    agy(SUBMIT.format(answers_file=answers_file, result_file=result_file, shot_name=f"{rec['id']}.png"),
        rdir / "agy_submit.log", continue_=True, minutes=10)
    result = read_json(result_file) or {}
    shot = find_shot(rec["id"], started)
    if result.get("submitted"):
        return finish(rec, "applied", result.get("confirmation", ""), env, rdir, answers, shot, review_note)
    return finish(rec, "needs_you", result.get("reason") or "Submit step didn't confirm.", env, rdir, answers,
                  shot, review_note)


# ---------- main ----------

def main():
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    env = json.loads(ENV_FILE.read_text(encoding="utf-8"))
    pc = load_pc()
    results = pc.get("results", {})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    results = {k: v for k, v in results.items() if v.get("at", "") >= cutoff}

    pc = {"jobs": scan(cfg), "results": results, "scanned_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
          "warned": pc.get("warned", "")}
    save_pc(pc)  # GitHub gets the new Wuzzuf/Bayt jobs even if applying fails below

    seen = load_seen()
    skip = words_regex(cfg.get("apply", {}).get("skip_titles"))
    queue = []
    for rec in sorted((r for r in seen.values() if r.get("status") == "queued" and r["id"] not in results),
                      key=lambda r: -r.get("score", 0)):
        if skip and skip.search(rec["title"]):  # internships etc.: never worth an application
            pc["results"][rec["id"]] = {"at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                                        "status": "skipped", "note": "title is in [apply] skip_titles"}
            log(f"[skipped] {rec['title']} | {rec['company']} (skip_titles)")
        else:
            queue.append(rec)
    queue = queue[: cfg.get("apply", {}).get("per_cycle", 4)]
    if not queue:
        log("[i] nothing queued")
        save_pc(pc)  # skipped ones still go back to GitHub
        return
    problems = ready_to_apply()
    if problems:
        today = datetime.now().date().isoformat()
        log(f"[!] can't apply: {problems}")
        if pc["warned"] != today:  # at most one warning a day
            notify(env, "⚠️ <b>جهازك مش قادر يقدّم دلوقتي</b>\n" + "\n".join(f"• {html.escape(p)}" for p in problems)
                   + f"\n{len(queue)} وظيفة مستنية في الطابور.")
            pc["warned"] = today
        save_pc(pc)
        return
    rules = audit.load_rules()
    for rec in queue:
        log(f"[>] applying: {rec['title']} | {rec['company']} | ⭐ {rec['score']}")
        try:
            pc["results"][rec["id"]] = apply_one(rec, cfg, env, rules)
        except Exception as e:  # one job going wrong must not stop the others
            pc["results"][rec["id"]] = finish(rec, "needs_you", f"runner error: {e}", env, RECEIPTS / rec["id"])
        save_pc(pc)  # after every job: a crash never loses a result


if __name__ == "__main__":
    main()
