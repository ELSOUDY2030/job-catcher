"""Your PC's half of Job Catcher. Windows Task Scheduler runs it every 30 minutes (pc/install_task.ps1).

1. Scan Wuzzuf + Bayt (they answer 403 to GitHub's servers, not to a home PC).
2. Apply to queued jobs (🤖 score >= [apply] auto_min_score, or 🚀 tapped on Telegram) with
   Antigravity (agy) + the job-hunter skill, in two steps:
     fill (stops before Submit, writes receipts/<id>/answers.json)
     -> supervisor: audit.py rules, then `claude -p` for answers the rules can't verify
     -> submit (screenshot + receipts/<id>/result.json)   ... only if nothing is wrong
   Every result goes to Telegram: screenshot, the questions and the answers, the review.
3. Hand jobs + results to GitHub through the "pc" branch (AES-encrypted with .state_key).

Needs: the job Edge profile signed in once (`pc_runner.py --login`; the runner starts it and Playwright MCP), agy with the
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
import jobsite                                 # noqa: E402
from prompts import ANSWER, BRIEF, FILL, RESUME, SUBMIT  # noqa: E402
import telegram_bot                            # noqa: E402
from sources import jobspy_boards, words_regex, wuzzuf  # noqa: E402

RECEIPTS = ROOT / "receipts"
LOG = RECEIPTS / "runner.log"
KEY_FILE = ROOT / ".state_key"
ENV_FILE = HERE / ".env.json"
FACTS = CV_DIR / "candidate_facts.md"
MY_ANSWERS = RECEIPTS / "my_answers.json"   # what you answered on Telegram, reused for the same question
AGY = shutil.which("agy") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe")
OPENSSL = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
MCP_PORT = 8931
# The browser: a separate Edge profile only for applying, reached over CDP (one connection shared by every agy
# session: no "connect.html" tab per session as with the extension, and each session sees the tabs of the last).
# A normal Edge window (not "controlled by automated software"); your own Edge profile is never touched.
# Its port is localhost-only, and only this profile listens on it.
EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
EDGE_PROFILE = Path(os.environ.get("LOCALAPPDATA", "")) / "JobCatcher" / "EdgeProfile"
CDP_PORT = 9341  # not 9222: Lenovo Vantage's WebView2 listens there
LOGIN_SITES = ["https://www.linkedin.com/login", "https://secure.indeed.com/auth", "https://wuzzuf.net/login",
               "https://www.bayt.com/en/login/"]
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


def parse_reply(text, n):
    """Your Telegram text -> {question number: answer}. "2: answer" lines; plain text if there is ONE question;
    "ok" / "تمام" = keep the suggestions ({}). None = can't tell which question it is for."""
    text = text.strip()
    if text.lower() in ("ok", "yes", "تمام", "موافق", "اه", "قدم", "قدّم"):
        return {}
    got = {int(m[1]): m[2].strip() for m in re.finditer(r"^\s*(\d+)\s*[:\-.)]\s*(.+)$", text, re.M)
           if 0 < int(m[1]) <= n}
    if got:
        return got
    return {1: text} if n == 1 else None


def ask_user(rec, open_qs, env, minutes):
    """Send the open questions with suggested answers; wait for ✅ / ⏭ / a text reply.
    -> {question: answer}, "skip", or None (no Telegram on this PC, or no reply in time)."""
    if not (env.get("token") and env.get("chat_id")) or not open_qs:
        return None
    chat = str(env["chat_id"])

    def tg(method, **kw):
        return telegram_bot.call(env["token"], method, **kw)

    # ponytail: no offset = never confirms updates, so GitHub's run still gets every 🚀 press; ids seen before
    # the question are ignored. Ceiling: 100 unconfirmed updates (GitHub confirms them every run).
    def poll():
        return tg("getUpdates", timeout=0, allowed_updates=["callback_query", "message"])

    last = max((u["update_id"] for u in poll()), default=0)
    lines = [f"❓ <b>{html.escape(rec['title'])}</b> · {html.escape(rec['company'])}", rec["url"], ""]
    for i, x in enumerate(open_qs, 1):
        opts = f"\n   ({' / '.join(map(str, x['options']))})" if x.get("options") else ""
        lines.append(f"{i}. {html.escape(str(x.get('q', '')) + opts)}\n   💡 {html.escape(str(x.get('suggested', '')))}")
    lines.append('\nموافق على الإجابات المقترحة؟ دوس ✅. عايز تغيّر واحدة: ابعت "2: إجابتك" (والباقي يفضل المقترح).')
    keys = {f"k:{rec['id']}", f"n:{rec['id']}"}
    sent = tg("sendMessage", chat_id=env["chat_id"], text="\n".join(lines)[:4000], parse_mode="HTML",
              disable_web_page_preview=True,
              reply_markup={"inline_keyboard": [[{"text": "✅ قدّم بالإجابات دي", "callback_data": f"k:{rec['id']}"},
                                                 {"text": "⏭ سيبها", "callback_data": f"n:{rec['id']}"}]]})
    log(f"[i] asked you on Telegram ({len(open_qs)} questions), waiting up to {minutes} min")
    answer = {str(x.get("q", "")): str(x.get("suggested", "")) for x in open_qs}

    def done(label):
        try:
            tg("editMessageReplyMarkup", chat_id=env["chat_id"], message_id=sent["message_id"],
               reply_markup={"inline_keyboard": [[{"text": label, "callback_data": "-"}]]})
        except Exception as e:
            log(f"[!] telegram: {e}")

    end = time.time() + minutes * 60
    while time.time() < end:
        time.sleep(5)
        try:
            updates = [u for u in poll() if u["update_id"] > last]
        except Exception as e:  # a network blip, or GitHub's getUpdates at the same moment (409): try again
            log(f"[!] telegram poll: {e}")
            continue
        for u in updates:
            last = max(last, u["update_id"])
            q, m = u.get("callback_query"), u.get("message")
            if q and str(q["message"]["chat"]["id"]) == chat and q.get("data") in keys:
                tg("answerCallbackQuery", callback_query_id=q["id"])
                if q["data"].startswith("n:"):
                    done("⏭ اتسابت")
                    return "skip"
                done("✅ بيقدّم")
                return answer
            if m and str(m["chat"]["id"]) == chat and m.get("text"):
                got = parse_reply(m["text"], len(open_qs))
                if got is None:
                    tg("sendMessage", chat_id=env["chat_id"], text='اكتب رقم السؤال: "2: إجابتك"')
                    continue
                for i, a in got.items():
                    answer[str(open_qs[i - 1].get("q", ""))] = a
                done("✅ بيقدّم بإجاباتك")
                return answer
    done("⌛ ماردّتش: محتاجك")
    return None


def remember(answers):
    known = read_json(MY_ANSWERS) or {}
    known.update(answers)
    MY_ANSWERS.write_text(json.dumps(known, ensure_ascii=False, indent=1), encoding="utf-8")


def known_answers():
    """Answers you gave on Telegram before, as a facts section (the brief and the review both get it)."""
    known = read_json(MY_ANSWERS) or {}
    return ("\n\n## Answers the user gave before (use the same answer for the same question)\n"
            + "\n".join(f"- {q}: {a}" for q, a in known.items())) if known else ""


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

def port_up(port):
    try:
        socket.create_connection(("localhost", port), timeout=3).close()
        return True
    except OSError:
        return False


def mcp_up():
    return port_up(MCP_PORT)


def ensure_edge(urls=(), wait=30):
    """The job Edge (EDGE_PROFILE) with its CDP port; started if it isn't running. urls: tabs to open."""
    if port_up(CDP_PORT) and not urls:
        return True
    EDGE_PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([str(EDGE), f"--user-data-dir={EDGE_PROFILE}", f"--remote-debugging-port={CDP_PORT}",
                      "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
                      # behind another window Edge stops drawing the page: clicks then wait for a "stable"
                      # element until they time out (25 of them on 2026-11-12)
                      "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                      "--disable-background-timer-throttling", *urls])
    for _ in range(wait // 3):
        if port_up(CDP_PORT):
            return True
        time.sleep(3)
    return False


def ensure_mcp(wait=90):
    """Playwright MCP on MCP_PORT, attached to the job Edge over CDP (no window). agy's mcp_config points at it."""
    if not ensure_edge():
        return False
    if mcp_up():
        return True
    log("[i] starting the Playwright MCP server (job Edge, CDP)")
    subprocess.Popen(["cmd", "/c", "npx", "-y", "@playwright/mcp@latest", "--cdp-endpoint",
                      f"http://127.0.0.1:{CDP_PORT}", "--port", str(MCP_PORT), "--shared-browser-context"],
                     cwd=CV_DIR, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        problems.append(f"the job Edge (CDP {CDP_PORT}) or Playwright MCP ({MCP_PORT}) didn't start")
    return problems


AGY_MODEL = "gemini-3.8-flash-medium"  # overridden by [apply] agy_model; list: `agy models`
AGY_FALLBACK = ["gemini-3.8-flash-high", "gemini-3.8-flash-low"]  # [apply] agy_fallback: when a model is out
# Never used in Antigravity, whatever the config or a fallback says (your call, 2026-11-12): Argon was slow
# (12 min for one Easy Apply) and its clicks kept timing out; Claude models run only through Claude Code
# (CV, review), never through Antigravity.
BLOCKED_MODELS = ("gemini-4-argon", "claude-")


AGENT_SRC = HERE / "job-applier.md"            # the agent: no shell tool at all, fixed system prompt
AGENT_DST = CV_DIR / ".agents" / "agents" / "job-applier" / "agent.md"
SETTINGS = HERE / "agy-cli-settings.json"       # permissions for these runs only (--settings), never saved


def install_agent():
    """agy finds workspace agents in <cwd>/.agents/agents/<name>/agent.md; the repo copy is the source."""
    text = AGENT_SRC.read_text(encoding="utf-8")
    if not AGENT_DST.exists() or AGENT_DST.read_text(encoding="utf-8") != text:
        AGENT_DST.parent.mkdir(parents=True, exist_ok=True)
        AGENT_DST.write_text(text, encoding="utf-8")


def agy(prompt, out, conversation=None, minutes=20):
    """One agy turn -> its JSON result {conversation_id, status, response, num_turns, duration_seconds}
    ({} if none). out = receipts/<id>/<step>: <step>.log is agy's log, <step>.json its result.
    conversation: resume that session (Submit continues the fill's session, in the same tab)."""
    # model: Gemini 3.8 Flash did the browser test in 106 s vs 242 s for Gemini 4 Argon (Medium);
    # the supervisor (audit.py + review) catches wrong answers either way.
    if AGY_MODEL.startswith(BLOCKED_MODELS):
        raise RuntimeError(f"{AGY_MODEL} is blocked (BLOCKED_MODELS)")
    cmd = [AGY, "--agent", "job-applier", "--settings", str(SETTINGS), "--output-format", "json",
           "--model", AGY_MODEL, "--print-timeout", f"{minutes}m", "--log-file", str(out.with_suffix(".log"))]
    cmd += (["--conversation", conversation] if conversation else []) + ["-p", prompt]
    try:
        stdout = subprocess.run(cmd, cwd=CV_DIR, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=(minutes + 5) * 60).stdout
    except subprocess.TimeoutExpired:
        stdout = ""
    out.with_suffix(".json").write_text(stdout or "", encoding="utf-8")
    res = review.parse_json(stdout) or {}
    log(f"[i] agy {out.name}: {res.get('status', 'no result')} in {res.get('duration_seconds') or 0:.0f}s, "
        f"{res.get('num_turns', '?')} turns")
    switch_model(out.with_suffix(".log"), str(res.get("error") or ""))
    return res


def switch_model(log_file, error="", limit=3):
    """The next runs use the next model in AGY_FALLBACK when this one is out (the resume after a failed fill
    then continues the same session on the new model):
    - Google's "No capacity available" (503): every step waits through retries (2 min/step on 2026-11-12),
      so after `limit` of them in one run;
    - "Individual quota reached": the model is gone for hours (Sonnet in agy, 2026-11-12), so at once."""
    global AGY_MODEL
    text = log_file.read_text(encoding="utf-8", errors="ignore") if log_file.exists() else ""
    fallback = [m for m in AGY_FALLBACK if not m.startswith(BLOCKED_MODELS)]
    if ("quota reached" in error or text.count("No capacity available") >= limit) and fallback:
        old, AGY_MODEL = AGY_MODEL, fallback[0]
        AGY_FALLBACK.remove(AGY_MODEL)
        log(f"[!] {old} is out (no capacity / quota): switching to {AGY_MODEL}")


def reply_json(run, key):
    """The JSON object in agy's reply, if it has `key`."""
    obj = review.parse_json(run.get("response") or "")
    return obj if isinstance(obj, dict) and key in obj else None


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


SKILL = CV_DIR / ".agents" / "skills" / "job-hunter" / "SKILL.md"
CV_MENU = CV_DIR / "cv_menu.md"
CHANNEL = {"easy_apply": "LinkedIn Easy Apply", "external": "LinkedIn -> company site", "no_button": "LinkedIn",
           "wuzzuf": "Wuzzuf", "bayt": "Bayt", "indeed": "Indeed", "external_direct": "company site"}
LOG_SECTION = "## Job Catcher (automatic)"
SCREEN_STEP = "2. Screen the job with the \"Screen\" rules below. If it isn't a fit: status \"skipped\" with the reason."


def prepared_answers(rdir):
    """Answers worked out BEFORE the browser (scout.json: the form's questions, answered from the facts), so the
    applier types instead of deciding. NOT_IN_FACTS ones are left to the "ask" rule."""
    scout = read_json(rdir / "scout.json") or {}
    rows = [f"- {x.get('q')}: {x.get('a')}" for x in scout.get("questions") or []
            if x.get("a") and "NOT_IN_FACTS" not in str(x.get("a"))]
    if not rows:
        return ""
    return ("\n\n## Prepared answers for this form (already checked against the facts: use them as written;\n"
            "the real form can word a question differently or ask others: those still come from the facts)\n"
            + "\n".join(rows) + (f"\nNote: {scout['notes']}" if scout.get("notes") else ""))


def write_brief(rec, rdir, cv_file, key):
    """Everything agy needs in ONE file: steps, this site's fixed recipe, skill rules (sections 2 + 4), facts.
    One read instead of SKILL.md + candidate_facts.md + cv_menu.md, and nothing left to look for."""
    if cv_file:
        cv_line = f"CV to upload: {cv_file} (already tailored to this job: use it, don't open it)"
        names = {"cv_file": str(cv_file), "cv_name": cv_file.name}
    else:
        cv_line = (f"CV: pick ONE file from this menu by the job's focus; its full path is {CV_DIR}\\<file>\n"
                   + CV_MENU.read_text(encoding="utf-8"))
        names = {"cv_file": "the CV you picked (full path)", "cv_name": "the CV you picked"}
    recipe = jobsite.RECIPES[key].format(company=rec["company"], **names)
    skill = SKILL.read_text(encoding="utf-8")
    rules = "\n\n".join(m.group(0).strip() for m in re.finditer(r"^## [24]\. .*?(?=^## )", skill, re.S | re.M))
    text = (BRIEF.format(title=rec["title"], company=rec["company"], location=rec["location"], url=rec["url"],
                         cv_line=cv_line, recipe=recipe, snapshot_rule=jobsite.SNAPSHOT_RULE)
            + "\n## Screen and apply rules (job-hunter skill)\n" + rules
            + prepared_answers(rdir)
            + "\n\n## Facts: the ONLY source for every answer\n" + FACTS.read_text(encoding="utf-8")
            + known_answers())
    if rec.get("screened"):  # Sonnet already screened the full job description
        text = text.replace(SCREEN_STEP, "2. The job was already screened from its description and passed: don't screen it again.")
    brief = rdir / "brief.md"
    brief.write_text(text, encoding="utf-8")
    return brief


def append_log(rec, key, answers, confirmation):
    """The applications_log.md row, written by code (agy used to read and rewrite the whole 700-line file)."""
    text = APPS_LOG.read_text(encoding="utf-8") if APPS_LOG.exists() else ""
    n = max((int(x) for x in re.findall(r"^\| (\d+) \|", text, re.M)), default=0) + 1
    head = "" if LOG_SECTION in text else (f"\n\n{LOG_SECTION}\n| # | Company | Role | Site | CV used | Notes |\n"
                                           "|---|---------|------|------|---------|-------|\n")
    qa = "; ".join(f"{x.get('q')}: {x.get('a')}" for x in answers.get("questions", []))
    cells = [str(n), rec["company"], f"{rec['title']} ({rec['location']})", f"{CHANNEL[key]} {rec['url']}",
             answers.get("cv_file", ""), f"{datetime.now():%d %b %Y} (Job Catcher); {qa}; Confirmed: '{confirmation}'"]
    row = "| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in cells) + " |\n"
    with APPS_LOG.open("a", encoding="utf-8") as f:
        f.write(head + row)


def apply_one(rec, cfg, env, rules, cv_file=None):
    rdir = RECEIPTS / rec["id"]
    rdir.mkdir(parents=True, exist_ok=True)
    for name in ("answers.json", "result.json", "fill.json", "fill2.json", "submit.json"):
        (rdir / name).unlink(missing_ok=True)
    started = time.time()

    # code first: which site and which fixed steps (LinkedIn's public page tells Easy Apply / external)
    key = jobsite.recipe_key(rec["url"])
    log(f"[i] steps: {key}")
    brief = write_brief(rec, rdir, cv_file, key)
    run = agy(FILL.format(brief=brief), rdir / "fill")
    answers = reply_json(run, "status")
    # ended early (e.g. a denied tool): resume once. Not after the time limit: that only doubles the wait.
    if not answers and run.get("conversation_id") and run.get("finished_reason") != "print_timeout":
        log(f"[i] no receipt yet, resuming the same session: {rec['title']}")
        run = agy(RESUME.format(brief=brief), rdir / "fill2", conversation=run["conversation_id"])
        answers = reply_json(run, "status")
    if not answers:
        return finish(rec, "needs_you", "Antigravity didn't finish filling the form (no receipt).", env, rdir)
    given = {}  # this job's answers from you on Telegram
    for n in range(3):  # a wizard can ask on more than one page
        if answers.get("status") != "ask":
            break
        reply = ask_user(rec, answers.get("open") or [], env, cfg.get("apply", {}).get("ask_minutes", 30))
        if reply is None:
            qs = " | ".join(str(x.get("q", "")) for x in answers.get("open") or [])
            return finish(rec, "needs_you", f"أسئلة مش في حقايقك (مافيش رد على Telegram): {qs}"[:600], env, rdir, answers)
        if reply == "skip":
            return finish(rec, "skipped", "انت قلت سيبها (Telegram)", env, rdir, answers)
        given.update(reply)
        remember(reply)
        run = agy(ANSWER.format(answers=json.dumps(reply, ensure_ascii=False), brief=brief), rdir / f"answer{n + 1}",
                  conversation=run.get("conversation_id"))
        answers = reply_json(run, "status")
        if not answers:
            return finish(rec, "needs_you", "Antigravity didn't finish after your answers (no receipt).", env, rdir)
    (rdir / "answers.json").write_text(json.dumps(answers, ensure_ascii=False, indent=1), encoding="utf-8")
    if answers.get("status") != "ready":
        status = "skipped" if answers.get("status") == "skipped" else "needs_you"
        return finish(rec, status, answers.get("reason", ""), env, rdir, answers)

    # supervisor: rules first, Claude only for what the rules can't verify
    known = {**(read_json(MY_ANSWERS) or {}), **given}
    mine = [x for x in answers.get("questions", []) if known.get(str(x.get("q"))) == str(x.get("a"))]
    verdict = audit.check([x for x in answers.get("questions", []) if x not in mine], rules)  # yours: no check
    review_note = f"🔎 مراجعة: {verdict['ok']} إجابة مطابقة للقواعد" + (f"، و{len(mine)} من إجاباتك" if mine else "")
    if verdict["unsure"]:
        if cfg.get("apply", {}).get("claude_review", True):
            try:
                facts = rdir / "facts_plus.md"  # + your Telegram answers, so a reused one isn't flagged
                facts.write_text(FACTS.read_text(encoding="utf-8") + known_answers(), encoding="utf-8")
                problems, who = review.review(rec, verdict["unsure"], facts)
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

    sub = agy(SUBMIT.format(answers=json.dumps(answers.get("questions", []), ensure_ascii=False),
                            shot_name=f"{rec['id']}.png"), rdir / "submit", conversation=run.get("conversation_id"),
              minutes=10)
    result = reply_json(sub, "submitted") or {}
    (rdir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    if result.get("submitted"):
        append_log(rec, key, answers, result.get("confirmation", ""))
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
    AGY_FALLBACK[:] = cfg.get("apply", {}).get("agy_fallback", AGY_FALLBACK)
    AGY_MODEL = review.AGY_MODEL = cfg.get("apply", {}).get("agy_model", AGY_MODEL)
    review.USE_CLAUDE = cfg.get("apply", {}).get("use_claude", True)
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
    save_pc(pc)  # skipped ones are kept even if this run is stopped (Ctrl+C) before the first job ends
    if not queue:
        log("[i] nothing queued")
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
    install_agent()
    # Sonnet makes the NEXT job's CV while Antigravity applies to this one ([apply] cv_model = "" turns it off)
    cv_model = cfg.get("apply", {}).get("cv_model", "claude-sonnet-5-5")
    with ThreadPoolExecutor(max_workers=1) as cv_pool:
        make_cv = lambda r: cv_pool.submit(cv_maker.make, r, FACTS, cv_model, log) if cv_model else None  # noqa: E731
        next_cv = make_cv(queue[0])
        for i, rec in enumerate(queue):
            cv_file, skip = next_cv.result() if next_cv else (None, "")
            next_cv = make_cv(queue[i + 1]) if i + 1 < len(queue) else None
            if skip:  # screened out from the job description: no browser run needed
                pc["results"][rec["id"]] = finish(rec, "skipped", skip, env, RECEIPTS / rec["id"])
                save_pc(pc)
                continue
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
    if "--login" in sys.argv:  # once: sign in to the job sites in the job Edge (it keeps the logins)
        ensure_edge(LOGIN_SITES)
        print("Sign in on each tab of the new Edge window (LinkedIn, Indeed, Wuzzuf, Bayt). Keep the window open.")
        sys.exit()
    lock = single_instance()
    if lock is None:
        log("[i] another runner is already working (scheduled task or --live), skipping")
    elif "--live" in sys.argv:
        live()
    else:
        main()
