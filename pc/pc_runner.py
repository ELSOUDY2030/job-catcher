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
import re
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
import cv_maker                                 # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from prompts import CV_MADE, CV_PICK, FILL, SUBMIT  # noqa: E402
from sources import jobspy_boards, words_regex, wuzzuf  # noqa: E402

RECEIPTS = ROOT / "receipts"
LOG = RECEIPTS / "runner.log"
KEY_FILE = ROOT / ".state_key"
ENV_FILE = HERE / ".env.json"
FACTS = CV_DIR / "candidate_facts.md"
AGY = shutil.which("agy") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe")
OPENSSL = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
MCP_PORT = 8931
# the scheduled task runs pythonw (no console): without this every claude/agy/git call opens a black window
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
    return subprocess.run(cmd, input=data, capture_output=True, creationflags=NO_WINDOW, check=True, env=env).stdout


def git(*args, cwd=ROOT, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, creationflags=NO_WINDOW, check=check)


def branch_file(branch, name):
    """Decrypted bytes of <name> on origin/<branch>, or None if the branch doesn't exist yet."""
    if git("fetch", "--quiet", "--depth", "1", "origin", branch, check=False).returncode:
        return None
    return crypt(git("show", f"FETCH_HEAD:{name}").stdout, decrypt=True)


GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"


def repo_slug(url):
    """'https://github.com/OWNER/REPO.git' or 'git@github.com:OWNER/REPO.git' -> 'OWNER/REPO'."""
    return url.strip().removesuffix(".git").replace("git@github.com:", "https://github.com/").split("github.com/")[-1]


def trigger_github():
    """GitHub's own 30-minute schedule is unreliable (it ran 2 of ~22 times on day one), so the PC,
    which runs every 30 minutes anyway, starts the workflow itself. The schedule stays as backup."""
    slug = repo_slug(git("remote", "get-url", "origin").stdout.decode())
    done = subprocess.run([GH, "workflow", "run", "job_watch.yml", "--repo", slug], capture_output=True, creationflags=NO_WINDOW, text=True)
    log("[i] GitHub run started" if done.returncode == 0 else f"[!] couldn't start the GitHub run: {done.stderr[:200]}")


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

def mcp_up():
    try:
        socket.create_connection(("localhost", MCP_PORT), timeout=3).close()
        return True
    except OSError:
        return False


def ensure_mcp(wait=60):
    """Start the Playwright MCP server (your start_playwright_mcp_hidden.vbs, no window) if it isn't running.
    With PLAYWRIGHT_MCP_EXTENSION_TOKEN set (pc/set_playwright_token.ps1) it attaches to Edge without a prompt."""
    if mcp_up():
        return True
    launcher = CV_DIR / "start_playwright_mcp_hidden.vbs"
    if not launcher.exists():
        return False
    log("[i] starting the Playwright MCP server")
    env = dict(os.environ)
    if "PLAYWRIGHT_MCP_EXTENSION_TOKEN" not in env:  # saved by set_playwright_token.ps1 after this process started
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                env["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = winreg.QueryValueEx(key, "PLAYWRIGHT_MCP_EXTENSION_TOKEN")[0]
        except OSError:
            pass
    subprocess.Popen(["wscript.exe", str(launcher)], cwd=CV_DIR, env=env)
    for _ in range(wait // 3):
        time.sleep(3)
        if mcp_up():
            return True
    return False


def ready_to_apply():
    problems = []
    if not Path(AGY).exists():
        problems.append("Antigravity CLI (agy) not found")
    if not audit.RULES_FILE.exists():
        problems.append("pc/facts_rules.toml missing")
    if not ensure_mcp():
        problems.append(f"Playwright MCP didn't start on port {MCP_PORT} (start_playwright_mcp.bat + Edge open)")
    return problems


AGY_MODEL = "gemini-3.8-flash-medium"  # overridden by [apply] agy_model; list: `agy models`


def conversation_id(log_file):
    """The agy session a log belongs to (printed as conversation=<uuid>), or None."""
    try:
        m = re.search(r"conversation=([0-9a-f-]{36})", Path(log_file).read_text(encoding="utf-8", errors="ignore"))
    except FileNotFoundError:
        return None
    return m.group(1) if m else None


def agy(prompt, log_file, continue_from=None, minutes=20):
    # model: Gemini 3.8 Flash (Low) did the browser test in 106 s vs 242 s for Gemini 4 Argon (Medium);
    # the supervisor (audit.py + claude -p) catches wrong answers either way.
    # continue_from = the fill step's log: Submit resumes THAT session (not "the most recent" one).
    conv = conversation_id(continue_from) if continue_from else None
    cmd = [AGY] + (["--conversation", conv] if conv else []) + [
        "--model", AGY_MODEL, "--print-timeout", f"{minutes}m", "--log-file", str(log_file), "-p", prompt]
    subprocess.run(cmd, cwd=CV_DIR, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=(minutes + 5) * 60)


EXAMPLE = HERE / "answers.example.json"  # agy reads this instead of searching folders with commands
APPS_LOG = CV_DIR / "applications_log.md"


def already_logged(rec, log_text=None):
    """Already in applications_log.md (applied, drafted or skipped before)? Then don't spend
    3-4 minutes of Antigravity on it. Match: the job's id from its URL, or company + title on one line."""
    if log_text is None:
        try:
            log_text = APPS_LOG.read_text(encoding="utf-8")
        except FileNotFoundError:
            return False
    text = log_text.lower()
    ids = re.findall(r"(?:jobs/view/|jobs/p/|/view/|jk=)([a-z0-9]{6,})", rec.get("url", "").lower())
    if any(i in text for i in ids):
        return True
    company = next((w for w in re.findall(r"\w+", rec.get("company", "").lower()) if len(w) >= 3), "")
    title = rec.get("title", "").lower().strip()
    return bool(company and title) and any(company in line and title in line for line in text.splitlines())


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
    if env.get("notify_each"):  # [apply] notify_each = true: a message per application (else: hourly summary only)
        if shot:
            notify(env, head, photo=rdir / "confirmation.png")
            if body:
                notify(env, f"📝 <b>الأسئلة والإجابات</b>\n{body}\n{review_note}")
        else:
            notify(env, "\n\n".join(p for p in (head, body, review_note) if p))
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="minutes"), "status": status, "note": (note or "")[:300],
             "proof": f"receipts/{rdir.name}/confirmation.png" if shot else ""}
    with (RECEIPTS / "index.jsonl").open("a", encoding="utf-8") as f:  # one line per job, for a quick review later
        f.write(json.dumps({"id": rec["id"], "title": rec["title"], "company": rec["company"], "url": rec["url"],
                            **entry}, ensure_ascii=False) + "\n")
    log(f"[{status}] {rec['title']} | {rec['company']} | {note}")
    return entry


def fill_prompt(rec, answers_file, cv_file=None):
    return FILL.format(cv_dir=CV_DIR, title=rec["title"], company=rec["company"], location=rec["location"],
                       score=rec["score"], url=rec["url"], answers_file=answers_file, example_file=EXAMPLE,
                       cv_step=CV_MADE.format(cv_file=cv_file) if cv_file else CV_PICK)


def apply_one(rec, cfg, env, rules, cv_file=None):
    rdir = RECEIPTS / rec["id"]
    rdir.mkdir(parents=True, exist_ok=True)
    answers_file, result_file = rdir / "answers.json", rdir / "result.json"
    for f in (answers_file, result_file):
        f.unlink(missing_ok=True)
    started = time.time()

    agy(fill_prompt(rec, answers_file, cv_file), rdir / "agy_fill.log")
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
                problems, who = review.review(rec, verdict["unsure"], FACTS)
                verdict["problems"] += problems
                review_note += f"، و{len(verdict['unsure'])} راجعها {who}"
            except Exception as e:
                verdict["problems"].append({"q": "-", "a": "-", "why": f"review failed: {e}"})
        else:
            review_note += f"، و{len(verdict['unsure'])} من غير مراجعة"
    (rdir / "audit.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
    if verdict["problems"]:
        why = "\n".join(f"• {p['q'][:70]} → {p['a'][:50]}: {p['why']}" for p in verdict["problems"][:6])
        return finish(rec, "blocked", f"المراجعة لقت مشكلة، فماتبعتش:\n{why}", env, rdir, answers, review_note=review_note)

    agy(SUBMIT.format(answers_file=answers_file, result_file=result_file, shot_name=f"{rec['id']}.png"),
        rdir / "agy_submit.log", continue_from=rdir / "agy_fill.log", minutes=10)
    result = read_json(result_file) or {}
    shot = find_shot(rec["id"], started)
    if result.get("submitted"):
        return finish(rec, "applied", result.get("confirmation", ""), env, rdir, answers, shot, review_note)
    return finish(rec, "needs_you", result.get("reason") or "Submit step didn't confirm.", env, rdir, answers,
                  shot, review_note)


# ---------- main ----------

def main():
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    env = read_json(ENV_FILE) or {}  # only needed for notify_each; the hourly summary comes from GitHub
    env["notify_each"] = cfg.get("apply", {}).get("notify_each", False)
    global AGY_MODEL
    AGY_MODEL = review.AGY_MODEL = cfg.get("apply", {}).get("agy_model", AGY_MODEL)
    pc = load_pc()
    results = pc.get("results", {})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    results = {k: v for k, v in results.items() if v.get("at", "") >= cutoff}

    pc = {"jobs": scan(cfg), "results": results, "scanned_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
          "warned": pc.get("warned", ""), "warnings": []}  # warnings show up in the hourly summary
    save_pc(pc)  # GitHub gets the new Wuzzuf/Bayt jobs even if applying fails below
    if cfg.get("apply", {}).get("trigger_github", True):
        trigger_github()  # it reads the pc branch we just pushed

    seen = load_seen()
    skip = words_regex(cfg.get("apply", {}).get("skip_titles"))
    queue = []
    for rec in sorted((r for r in seen.values() if r.get("status") == "queued" and r["id"] not in results),
                      key=lambda r: -r.get("score", 0)):
        reason = ("تدريب (internship): مش بنقدّم عليه" if skip and skip.search(rec["title"])  # never worth it
                  else "موجودة في applications_log.md (اتقدّم عليها أو اتسابت قبل كده)" if already_logged(rec)
                  else None)
        if reason:
            pc["results"][rec["id"]] = {"at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                                        "status": "skipped", "note": reason}
            log(f"[skipped] {rec['title']} | {rec['company']} ({reason})")
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
        pc["warnings"] = [f"جهازك مش قادر يقدّم: {p}" for p in problems]  # -> hourly summary
        if env["notify_each"] and pc["warned"] != today:  # old mode: at most one separate warning a day
            notify(env, "⚠️ <b>جهازك مش قادر يقدّم دلوقتي</b>\n" + "\n".join(f"• {html.escape(p)}" for p in problems)
                   + f"\n{len(queue)} وظيفة مستنية في الطابور.")
            pc["warned"] = today
        save_pc(pc)
        return
    rules = audit.load_rules()
    # Sonnet makes the NEXT job's CV while Antigravity applies to this one ([apply] cv_model = "" turns it off)
    cv_model = cfg.get("apply", {}).get("cv_model", "claude-sonnet-5-5")
    with ThreadPoolExecutor(max_workers=1) as cv_pool:
        make_cv = lambda r: cv_pool.submit(cv_maker.make, r, FACTS, cv_model, log) if cv_model else None  # noqa: E731
        next_cv = make_cv(queue[0])
        for i, rec in enumerate(queue):
            cv_file = next_cv.result() if next_cv else None
            next_cv = make_cv(queue[i + 1]) if i + 1 < len(queue) else None
            log(f"[>] applying: {rec['title']} | {rec['company']} | ⭐ {rec['score']} | CV: "
                f"{cv_file.name if cv_file else 'from cv_menu.md'}")
            try:
                pc["results"][rec["id"]] = apply_one(rec, cfg, env, rules, cv_file)
            except Exception as e:  # one job going wrong must not stop the others
                pc["results"][rec["id"]] = finish(rec, "needs_you", f"runner error: {e}", env, RECEIPTS / rec["id"])
            save_pc(pc)  # after every job: a crash never loses a result


def single_instance():
    """The scheduled task and --live must never apply at the same time (same job twice).
    The lock lives as long as the process (released on exit or crash). None = someone else has it."""
    import msvcrt
    RECEIPTS.mkdir(exist_ok=True)
    f = open(RECEIPTS / "runner.lock", "w")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f


def live(every=30):
    """`python pc/pc_runner.py --live`: a run every 30 minutes in this window, Ctrl+C stops it."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Arabic notes in a cp1252 console
    try:
        while True:
            start = time.time()
            try:
                main()
            except Exception as e:  # one bad run must not end the loop
                log(f"[!] run failed: {e}")
            wait = max(0, start + every * 60 - time.time())
            log(f"[i] next run at {datetime.now() + timedelta(seconds=wait):%H:%M}  (Ctrl+C to stop)")
            time.sleep(wait)
    except KeyboardInterrupt:  # subprocess.run kills the running agy / claude on the way out
        log("[i] stopped (Ctrl+C)")


if __name__ == "__main__":
    lock = single_instance()
    if lock is None:
        log("[i] another runner is already working (scheduled task or --live), skipping")
    elif "--live" in sys.argv:
        live()
    else:
        main()
