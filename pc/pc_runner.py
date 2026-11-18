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
from collections import Counter
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
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
from prompts import ANSWER, BRIEF, FILL, RESUME, SEARCH, SEARCH_BRIEF, SUBMIT  # noqa: E402
import telegram_bot                            # noqa: E402
from sources import jobspy_boards, make_job, words_regex, wuzzuf  # noqa: E402

RECEIPTS = ROOT / "receipts"
LOG = RECEIPTS / "runner.log"
KEY_FILE = ROOT / ".state_key"
ENV_FILE = HERE / ".env.json"
FACTS = CV_DIR / "candidate_facts.md"
PENDING = RECEIPTS / "pending_asks.json"  # questions you didn't answer in time: {job id: {msg, open}}
REQUEUE = RECEIPTS / "requeue.json"       # {job id: "apply" | "skip"} from a late answer, for the next run
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
               "https://www.bayt.com/en/login/",
               "https://accounts.google.com/", "https://www.naukrigulf.com/jobseeker/login",
               "https://www.gulftalent.com/login"]  # for "Sign in with Google" on Workday / SuccessFactors / Ashby...
# the scheduled task runs pythonw (no console): without this every claude/agy/git call opens a black window
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
KEEP_DAYS = 30


if sys.stdout:  # pythonw has none; a cp1252 console crashed on "⭐" before the first job (14 Nov 2026)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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


def tag(rec):
    return f"#{rec['n']}" if rec.get("n") else rec["id"]


def ask_user(rec, open_qs, env, minutes):
    """Send the open questions with suggested answers; the inbox thread hands back ✅ / ⏭ / your text.
    -> {question: answer}, "skip", or None (no Telegram on this PC, or no reply in time: kept in PENDING,
    so a later "اعتمد <n>" or ✅ still re-applies it)."""
    if not (env.get("token") and env.get("chat_id")) or not open_qs:
        return None
    start_inbox(env)
    lines = [f"❓ {tag(rec)} <b>{html.escape(rec['title'])}</b> · {html.escape(rec['company'])}", rec["url"], ""]
    for i, x in enumerate(open_qs, 1):
        opts = f"\n   ({' / '.join(map(str, x['options']))})" if x.get("options") else ""
        lines.append(f"{i}. {html.escape(str(x.get('q', '')) + opts)}\n   💡 {html.escape(str(x.get('suggested', '')))}")
    t = tag(rec).lstrip("#")
    lines.append(f'\nموافق؟ دوس ✅ أو اكتب "اعتمد {t}". تغيّر إجابة: "{t} 2: إجابتك". مش عايزها: "سيب {t}".')
    sent = telegram_bot.call(env["token"], "sendMessage", chat_id=env["chat_id"], text="\n".join(lines)[:4000],
                             parse_mode="HTML", disable_web_page_preview=True,
                             reply_markup={"inline_keyboard": [[
                                 {"text": "✅ قدّم بالإجابات دي", "callback_data": f"k:{rec['id']}"},
                                 {"text": "⏭ سيبها", "callback_data": f"n:{rec['id']}"}]]})
    log(f"[i] asked you on Telegram ({len(open_qs)} questions), waiting up to {minutes} min")
    with LOCK:
        WAITING[rec["id"]] = {"open": open_qs, "msg": sent["message_id"], "result": None}
    end = time.time() + minutes * 60
    while time.time() < end and WAITING[rec["id"]]["result"] is None:
        time.sleep(2)
    with LOCK:
        ask = WAITING.pop(rec["id"])
        if ask["result"] is None:
            pending = read_json(PENDING) or {}
            pending[rec["id"]] = {"msg": ask["msg"], "open": open_qs}
            PENDING.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
    return ask["result"]


def asked_before(job_id):
    """Open questions of a job whose wait ended before pending_asks.json existed: from its newest agy receipt."""
    rdir = RECEIPTS / job_id
    runs = (sorted(rdir.glob("answer*.json"), reverse=True) + [rdir / "fill.json"]) if rdir.exists() else []
    old = next((r for r in (reply_json(read_json(f) or {}, "status") for f in runs) if r), None) or {}
    return old.get("open") or [] if old.get("status") == "ask" else []


# ---------- Telegram chat: "قدم 111", "اعتمد 111", "111 2: answer", "سيب 111", "افتح 111", "111" ----------
WAITING = {}       # job id -> {"open", "msg", "result"}: ask_user is waiting on it right now
JOBS = {}          # "111" -> record (from the last load_seen) + every record by id
LOCK = threading.RLock()
INBOX = {}         # the running inbox thread (one per process)
VERBS = [("apply", r"قد[ّ]?م|قدملي|apply"), ("ok", r"اعتمد|موافق|ok|approve"), ("skip", r"سيب(?:ها)?|skip"),
         ("open", r"افتح|open"), ("status", r"حالة|ايه|إيه|status")]
REGIONS = [("🇪🇬 مصر", r"مصر|egypt"),  # same order as region_rank()
           ("🏜 الخليج", r"(?:ال)?خليج|gulf|(?:ال)?سعودي[هةا]?|(?:ال)?[اإ]مارات|قطر|الكويت|ksa|uae"),
           ("🌍 Remote", r"ريموت|remote"),
           ("🇪🇺 أوروبا وغيرها", r"[اأ]وروبا|europe|الباقي|غير[هة]ا")]
BUCKETS = [("applied", "✅ اتقدّم"), ("queued", "⏳ فاضل"), ("needs_you", "✋ محتاجك"),
           ("rejected", "⛔ اترفض/اتساب"), ("new", "👀 برا التلقائي")]
REPORT_EVERY = 30 * 60  # the region report on Telegram, while the runner is up
HELP = ("الأوامر (الرقم هو #رقم الوظيفة في الملخص):\n"
        "• مصر / الخليج / ريموت / أوروبا: كل تفاصيل المنطقة\n"
        "• قدم 111: يقدّم عليها في الدورة الجاية\n• اعتمد 111: يقدّم بالإجابات المقترحة\n"
        "• 111 2: إجابتك: يغيّر إجابة السؤال 2 ويقدّم\n• سيب 111: يسيبها\n• افتح 111: يفتحها في Edge\n"
        "• 111: حالتها وأسئلتها")


def parse_command(text):
    """Your message -> (verb, job ref or None, rest). No network."""
    t = text.strip()
    for i, (_, words) in enumerate(REGIONS):
        if re.fullmatch(words, t, re.I):
            return "region", str(i), ""
    for verb, words in VERBS:
        m = re.match(rf"^(?:{words})(?:\s+#?(.+?))?\s*$", t, re.I | re.S)
        if m:
            return verb, (m[1] or "").strip() or None, ""
    m = re.match(r"^#?(\d+)\s+(\d+\s*[:\-.)]\s*.+)$", t, re.S)
    if m:
        return "edit", m[1], m[2].strip()
    m = re.match(r"^#?(\d+)$", t)
    if m:
        return "status", m[1], ""
    if re.match(r"^\d+\s*[:\-.)]\s*\S", t):
        return "edit", None, t
    return "help", None, t


def find_job(ref, reply_to=""):
    """'111' / a job id / a company name / None (the one job waiting for you) -> record or an error line."""
    with LOCK:
        waiting = list(WAITING) + list(read_json(PENDING) or {})
    if ref is None:
        hit = re.search(r"#(\d+)", reply_to or "")
        ref = hit[1] if hit else None
    if ref is None:
        if len(waiting) == 1:
            return JOBS.get(waiting[0]) or {"id": waiting[0], "title": waiting[0], "company": "", "url": ""}
        return "مستني إجاباتك على أكتر من وظيفة: اكتب رقمها، مثلاً \"اعتمد 111\"." if waiting else HELP
    rec = JOBS.get(ref.lstrip("#"))
    if rec:
        return rec
    hits = [r for r in JOBS.values() if r.get("n") and ref.lower() in f"{r.get('company', '')} {r.get('title', '')}".lower()]
    if len({r["id"] for r in hits}) == 1:
        return hits[0]
    return f"مالقيتش وظيفة #{ref}." if not hits else f"فيه أكتر من وظيفة لـ «{ref}»: " + ", ".join(
        sorted({tag(r) for r in hits})[:8])


def queue_again(job_id, what):
    with LOCK:
        q = read_json(REQUEUE) or {}
        q[job_id] = what
        REQUEUE.write_text(json.dumps(q), encoding="utf-8")


def requeue_done(job_id):
    with LOCK:
        q = read_json(REQUEUE) or {}
        if q.pop(job_id, None) is not None:
            REQUEUE.write_text(json.dumps(q), encoding="utf-8") if q else REQUEUE.unlink(missing_ok=True)


def handle(verb, ref, rest, reply_to=""):
    """One command -> the line the bot answers. Acts through WAITING (a job waiting now) or REQUEUE (next run)."""
    if verb == "help":
        return HELP
    if verb == "region":
        return region_detail(int(ref))
    rec = find_job(ref, reply_to)
    if isinstance(rec, str):
        return rec
    jid, name = rec["id"], f"{tag(rec)} {rec.get('title', '')} · {rec.get('company', '')}"
    with LOCK:
        pending = read_json(PENDING) or {}
        ask = WAITING.get(jid) or pending.get(jid) or ({"open": asked_before(jid)} if verb in ("ok", "edit") else None)
    if verb == "status":
        row = next((r for r in reversed(index_rows()) if r["id"] == jid), {})
        st = row.get("status") or rec.get("status", "")
        lines = [f"{name}\n{rec.get('url', '')}\nالحالة: {st} {row.get('note', '')}"[:900]]
        lines += [f"{i}. {x.get('q')}\n   💡 {x.get('suggested')}" for i, x in enumerate((ask or {}).get("open") or [], 1)]
        return "\n".join(lines)
    if verb == "open":
        return reopen(jid)
    if verb == "skip":
        with LOCK:
            if jid in WAITING:
                WAITING[jid]["result"] = "skip"
            else:
                pending.pop(jid, None)
                PENDING.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
                queue_again(jid, "skip")
        return f"⏭ {name}: اتسابت."
    if verb == "apply" and not (ask and ask.get("open")):
        if rec.get("status") == "applied":
            return f"{name}: اتقدّم عليها قبل كده ✅"
        queue_again(jid, "apply")
        return f"🚀 {name}: هيقدّم عليها في الدورة الجاية."
    if not (ask and ask.get("open")):  # ok / edit with nothing asked
        queue_again(jid, "apply")
        return f"🚀 {name}: مافيش أسئلة مستنياك فيها، فهيقدّم عليها في الدورة الجاية."
    got = parse_reply(rest, len(ask["open"])) if verb == "edit" else {}
    if got is None:
        return f'اكتب رقم السؤال: "{tag(rec).lstrip("#")} 2: إجابتك"'
    answer = {str(x.get("q", "")): str(x.get("suggested", "")) for x in ask["open"]}
    answer.update({str(ask["open"][i - 1].get("q", "")): a for i, a in got.items()})
    with LOCK:
        if jid in WAITING:
            WAITING[jid]["result"] = answer
            return f"✅ {name}: بيكمّل التقديم بإجاباتك دلوقتي."
        remember(answer)
        pending.pop(jid, None)
        PENDING.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
        queue_again(jid, "apply")
    return f"✅ {name}: اتحفظت إجاباتك، وهيقدّم عليها في الدورة الجاية."


def index_rows():
    f = RECEIPTS / "index.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()] if f.exists() else []


def job_states():
    """id -> (record, bucket, note): GitHub's record, with this PC's latest receipt on top."""
    last = {r["id"]: r for r in index_rows()}
    kind = {"applied": "applied", "queued": "queued", "needs_you": "needs_you", "ask": "needs_you",
            "skipped": "rejected", "blocked": "rejected"}
    with LOCK:
        recs = {r["id"]: r for r in JOBS.values() if r.get("id") and r.get("title")}
    return {jid: (rec, kind.get(last.get(jid, {}).get("status") or rec.get("status"), "new"),
                  last.get(jid, {}).get("note", "")) for jid, rec in recs.items()}


def region_report():
    states, today = job_states().values(), datetime.now(timezone.utc).date().isoformat()  # ponytail: UTC day
    lines = ["📊 المناطق (كل نص ساعة). ابعت اسم المنطقة للتفاصيل، مثلاً: مصر"]
    for i, (name, _) in enumerate(REGIONS):
        rows = [x for x in states if region_rank(x[0]) == i]
        if rows:
            c = Counter(b for _, b, _ in rows)
            new = sum(r.get("first_seen", "")[:10] == today for r, _, _ in rows)
            lines.append(f"\n{name}: لقى {len(rows)} (النهارده {new})\n"
                         + " | ".join(f"{label} {c[b]}" for b, label in BUCKETS if c[b]))
    return "\n".join(lines)


def region_detail(i, per_list=12):
    rows = sorted((x for x in job_states().values() if region_rank(x[0]) == i),
                  key=lambda x: x[0].get("first_seen", ""), reverse=True)
    if not rows:
        return f"{REGIONS[i][0]}: مافيش وظايف لسه."
    c = Counter(b for _, b, _ in rows)
    out = [f"{REGIONS[i][0]}: لقى {len(rows)}\n" + " | ".join(f"{label} {c[b]}" for b, label in BUCKETS if c[b])]
    for b, label in BUCKETS:
        hits = [x for x in rows if x[1] == b]
        if hits:
            out.append(f"\n{label} ({len(hits)}):")
            for r, _, note in hits[:per_list]:
                why = f"\n   ↳ {note[:90]}" if note and b in ("needs_you", "rejected") else ""
                out.append(f"• {tag(r)} {r.get('title', '')[:55]} · {r.get('company', '')[:30]}{why}")
            if len(hits) > per_list:
                out.append(f"  … و{len(hits) - per_list} كمان")
    return "\n".join(out)[:4000]


def on_update(u, env):
    """One Telegram update from your chat -> handle() -> the bot's answer."""
    q, m = u.get("callback_query"), u.get("message") or {}
    chat = str(env["chat_id"])
    if q and str(q.get("message", {}).get("chat", {}).get("id")) == chat:
        action, _, jid = (q.get("data") or "").partition(":")
        if action not in ("k", "n"):
            return  # 🚀 and ✅/❌ buttons: GitHub's run handles them
        JOBS.setdefault(jid, {"id": jid, "title": "", "company": "", "url": ""})
        answer = handle("ok" if action == "k" else "skip", jid, "")
        telegram_bot.call(env["token"], "answerCallbackQuery", callback_query_id=q["id"], text=answer[:190])
        telegram_bot.call(env["token"], "editMessageReplyMarkup", chat_id=chat, message_id=q["message"]["message_id"],
                          reply_markup={"inline_keyboard": [[{"text": answer[:60], "callback_data": "-"}]]})
    elif str(m.get("chat", {}).get("id")) == chat and m.get("text"):
        answer = handle(*parse_command(m["text"]), reply_to=(m.get("reply_to_message") or {}).get("text", ""))
        telegram_bot.call(env["token"], "sendMessage", chat_id=chat, text=answer[:4000], disable_web_page_preview=True)
    else:
        return
    log(f"[i] telegram: {answer.splitlines()[0][:150]}")


def inbox(env, stop, every=3):
    """Reads your chat every few seconds while the runner is up. ponytail: getUpdates without offset never
    confirms anything, so GitHub's run still gets every 🚀 press; ceiling: 100 unconfirmed updates."""
    last_file = RECEIPTS / "tg_open_last.txt"
    last = int(last_file.read_text() or 0) if last_file.exists() else None
    next_report = time.time() + 60  # the first one once the run has loaded the jobs
    while not stop.wait(every):
        if time.time() >= next_report and JOBS:
            next_report = time.time() + REPORT_EVERY
            try:
                telegram_bot.call(env["token"], "sendMessage", chat_id=str(env["chat_id"]), text=region_report())
            except Exception as e:
                log(f"[!] region report: {e}")
        try:
            updates = telegram_bot.call(env["token"], "getUpdates", timeout=0,
                                        allowed_updates=["callback_query", "message"])
        except Exception:  # a network blip, or GitHub's getUpdates at the same moment (409)
            continue
        if last is None:  # first start: don't replay old messages
            last = max((x["update_id"] for x in updates), default=0)
        for u in updates:
            if u["update_id"] > last:
                last = u["update_id"]
                try:
                    on_update(u, env)
                except Exception as e:
                    log(f"[!] telegram command: {e}")
        last_file.write_text(str(last))


def start_inbox(env):
    if INBOX or not (env.get("token") and env.get("chat_id")):
        return
    INBOX["stop"] = threading.Event()
    INBOX["thread"] = threading.Thread(target=inbox, args=(env, INBOX["stop"]), daemon=True)
    INBOX["thread"].start()


QUESTIONS_FILE = RECEIPTS / "questions_to_answer.md"


def stopping_questions():
    """Every question that stopped (asked) or blocked (review) a job, not answered yet -> [{q, sug, jobs, why}]."""
    known = {k.lower() for k in (read_json(MY_ANSWERS) or {})}
    names = {x["id"]: x.get("company", x["id"]) for x in index_rows()}
    found = {}

    def add(q, sug, job, why):
        q = " ".join(str(q or "").split()).rstrip("*").strip()
        if q and q.lower() not in known:
            e = found.setdefault(q.lower(), {"q": q, "sug": str(sug or ""), "jobs": [], "why": why})
            if job not in e["jobs"]:
                e["jobs"].append(job)

    for d in sorted(x for x in RECEIPTS.iterdir() if x.is_dir()):
        job = names.get(d.name, d.name)
        for f in sorted(d.glob("*.json")):
            j = read_json(f)
            rep_ = reply_json(j, "status") if isinstance(j, dict) and "response" in j else None
            for x in (rep_ or {}).get("open") or [] if (rep_ or {}).get("status") == "ask" else []:
                add(x.get("q"), x.get("suggested"), job, "")
        for x in (read_json(d / "audit.json") or {}).get("problems", []):
            add(x.get("q"), x.get("a"), job, str(x.get("why", "")))
    return list(found.values())


def write_questions():
    """receipts/questions_to_answer.md: write your answer under each question, then --import-answers."""
    qs = stopping_questions()
    out = ["# أسئلة وقفت التقديم", "",
           "اكتب إجابتك بعد «إجابتك:». «ok» = خد المقترح. سيبها فاضية = اسأل عليها تاني على Telegram.",
           "بعدها: `.venv\\Scripts\\python pc\\pc_runner.py --import-answers`", ""]
    for i, e in enumerate(qs, 1):
        out += [f"### {i}. {e['q']}", f"- الوظايف: {', '.join(e['jobs'])[:200]}", f"- المقترح: {e['sug'][:300]}"]
        if e["why"]:
            out.append(f"- المراجع وقفها ليه: {e['why'][:250]}")
        out += ["- إجابتك: ", ""]
    QUESTIONS_FILE.write_text("\n".join(out), encoding="utf-8")
    return len(qs)


def import_answers():
    """Your answers in questions_to_answer.md -> my_answers.json (the brief and the review use them)."""
    text = QUESTIONS_FILE.read_text(encoding="utf-8")
    got = {}
    for block in re.split(r"^### \d+\. ", text, flags=re.M)[1:]:
        q = block.splitlines()[0].strip()
        sug = re.search(r"^- المقترح: (.*)$", block, re.M)
        ans = re.search(r"^- إجابتك:[ \t]*(.*)$", block, re.M)
        a = (ans[1] if ans else "").strip()
        if a:
            got[q] = (sug[1].strip() if sug else "") if a.lower() in ("ok", "تمام", "موافق") else a
    remember(got)
    return len(got)


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
    try:
        found = agent_search(cfg.get("agent_search", {}))
    except Exception as e:
        log(f"[!] agent search: {e}")
        found = []
    jobs += [{**j, "posted": j["posted"].isoformat() if j.get("posted") else None} for j in found]
    return jobs


SEARCH_LAST = RECEIPTS / "agent_search_last.txt"
SITE_URL = {"naukrigulf": re.compile(r"^https://www\.naukrigulf\.com/[a-z0-9-]+-jid-\d+"),
            "gulftalent": re.compile(r"^https://www\.gulftalent\.com/[a-z-]+/jobs/[a-z0-9-]+-\d+")}


def search_jobs(reply):
    """Hydrogen's search reply -> jobs. Only links of the searched sites, trimmed to the job page (no tokens)."""
    jobs = []
    for x in (reply or {}).get("jobs") or []:
        url = str(x.get("url") or "")
        for site, pattern in SITE_URL.items():
            m = pattern.match(url)
            if m and x.get("title"):
                jobs.append(make_job(x["title"], x.get("company", ""), x.get("location", ""), m[0],
                                     x.get("posted") or None, site))
    return jobs


def agent_search(section):
    """Once a day ([agent_search] every_hours): Hydrogen reads the search pages of NaukriGulf / GulfTalent in the
    job Edge, like you would. They block scripts, so this (and their alert emails) is how their jobs come in."""
    if not section.get("enabled", False) or not section.get("pages"):
        return []
    last = SEARCH_LAST.read_text().strip() if SEARCH_LAST.exists() else ""
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=section.get("every_hours", 24)):
        return []
    if not ensure_mcp():
        return []
    install_agent()
    rdir = RECEIPTS / "_search"
    rdir.mkdir(parents=True, exist_ok=True)
    brief = rdir / "brief.md"
    brief.write_text(SEARCH_BRIEF.format(today=datetime.now().date().isoformat(),
                                         pages="\n".join(f"- {u}" for u in section["pages"])), encoding="utf-8")
    run = agy(SEARCH.format(brief=brief), rdir / "search", minutes=section.get("minutes", 15))
    jobs = search_jobs(reply_json(run, "jobs"))
    SEARCH_LAST.write_text(datetime.now(timezone.utc).isoformat(timespec="minutes"))  # even 0: once a day is enough
    log(f"[i] agent search (NaukriGulf / GulfTalent): {len(jobs)} jobs")
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


USER_TABS = RECEIPTS / "user_tabs.json"  # tabs reopened for you: never closed by the runner


def cdp(path, method="GET"):
    """The job Edge's DevTools HTTP endpoint (/json/list, /json/new, /json/close)."""
    with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}/json/{path}", method=method),
                                timeout=10) as r:
        body = r.read()
    return json.loads(body) if body.lstrip()[:1] in (b"{", b"[") else {}


def job_pages():
    """Real pages, most recently used first, without the ones reopened for you."""
    keep = set(read_json(USER_TABS) or [])
    return [t for t in cdp("list") if t["type"] == "page" and t["id"] not in keep]


def stop_url():
    """The page a stopped job is on (saved, so it can be reopened after its tab is closed)."""
    try:
        return next((t["url"] for t in job_pages() if t["url"].startswith("http")), "")
    except (OSError, ValueError):
        return ""


def close_tabs():
    """Every job Edge tab closed after each job, except one blank one (Edge closes with its last tab) and the
    ones reopened for you. A stopped job's page is in its result ("stop_url"): `--open <company>` or
    "افتح <company>" on Telegram brings it back."""
    try:
        open_ = {t["id"] for t in cdp("list") if t["type"] == "page"}
        USER_TABS.write_text(json.dumps(sorted(set(read_json(USER_TABS) or []) & open_)))  # forget closed ones
        pages = job_pages()
        blank = next((t for t in pages if not t["url"].startswith("http")), None) or cdp("new?about:blank", "PUT")
        for t in pages:
            if t["id"] != blank.get("id"):
                cdp(f"close/{t['id']}")
    except (OSError, ValueError) as e:
        log(f"[!] closing tabs: {e}")


def unminimize():
    """A minimized Edge draws nothing, so every click waits for a "stable" element until it times out
    (6 NaukriGulf jobs in a row on 2026-11-17 21:55-22:05). The flags in ensure_edge only cover a window
    behind others. -> True if it had to restore the window."""
    try:
        import websocket  # websocket-client
        page = next(t for t in cdp("list") if t["type"] == "page")
        ws = websocket.create_connection(cdp("version")["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
        try:
            def call(i, method, params):
                ws.send(json.dumps({"id": i, "method": method, "params": params}))
                while (r := json.loads(ws.recv())).get("id") != i:
                    pass
                return r.get("result") or {}
            win = call(1, "Browser.getWindowForTarget", {"targetId": page["id"]})
            if win.get("bounds", {}).get("windowState") != "minimized":
                return False
            call(2, "Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "normal"}})
            log("[i] the job Edge was minimized: restored it (clicks time out in a minimized window)")
            return True
        finally:
            ws.close()
    except Exception as e:  # no Edge yet / no websocket-client: the run goes on as before
        log(f"[!] unminimize: {e}")
        return False


def reopen(query):
    """'Adria' / a job id -> the newest stopped job that matches, opened in the job Edge. -> a line for you."""
    rows = [json.loads(x) for x in (RECEIPTS / "index.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    q = query.lower()
    hit = next((r for r in reversed(rows) if r.get("status") in ("needs_you", "blocked")
                and (r["id"] == query or q in r["company"].lower() or q in r["title"].lower())), None)
    if not hit:
        return f"مالقيتش وظيفة واقفة اسمها «{query}»."
    if not ensure_edge():
        return "Edge بتاع الوظايف مش راضي يفتح."
    tab = cdp("new?" + (hit.get("stop_url") or hit["url"]), "PUT")
    USER_TABS.write_text(json.dumps(sorted(set(read_json(USER_TABS) or []) | {tab.get("id")})))
    return f"🔗 فتحتلك {hit['title']} · {hit['company']} في Edge بتاع الوظايف (مش هيتقفل لحد ما تقفله انت)."


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


AGY_MODEL = "gemini-4-hydrogen-medium"  # overridden by [apply] agy_model; list: `agy models`
AGY_FALLBACK = ["gemini-4-hydrogen-high", "gemini-4-hydrogen-low"]  # [apply] agy_fallback: when a model is out
# Never used in Antigravity, whatever the config or a fallback says (your call, 2026-11-12): Argon was slow
# (12 min for one Easy Apply) and its clicks kept timing out; Claude models run only through Claude Code
# (CV, review), never through Antigravity.
BLOCKED_MODELS = ("gemini-4-argon", "gemini-4-dragon", "gemini-3.8-flash", "claude-")


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
    if "empty conversation id" in str(res.get("error") or "") and not getattr(agy, "retrying", False):
        log("[i] agy didn't start (backend blip): one more try in 20s")  # 2 jobs lost to it on 2026-11-17
        time.sleep(20)
        agy.retrying = True
        try:
            return agy(prompt, out, conversation, minutes)
        finally:
            agy.retrying = False
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
START_FILE = RECEIPTS / "start_after.txt"  # jobs GitHub found before this time are never applied to


from sources import EGYPT  # noqa: E402  (same list GitHub uses to auto-queue every Egypt job)
GULF = re.compile(r"\b(united arab emirates|uae|dubai|abu dhabi|sharjah|saudi arabia|saudi|ksa|riyadh|jeddah|dammam|"
                  r"khobar|qatar|doha|kuwait|bahrain|manama|oman|muscat)\b", re.I)
REMOTE = re.compile(r"\b(remote|worldwide|anywhere|remotive|remoteok|himalayas|jobicy)\b", re.I)


def region_rank(rec):
    """The user's order (14 Nov 2026): Egypt, then the Gulf, then remote, then Europe and the rest.
    The score only orders jobs inside the same region."""
    where = f"{rec.get('location', '')} {rec.get('source', '')}"
    return 0 if EGYPT.search(where) else 1 if GULF.search(where) else 2 if REMOTE.search(where) else 3


assert [region_rank({"location": x}) for x in ("Cairo, Egypt", "Dubai, UAE", "Remote", "Berlin, Germany")] == [0, 1, 2, 3]


def is_old(rec, since):
    """Posted before the day the system was first started (receipts/start_after.txt)? Undated jobs: found
    before that moment. ponytail: posted dates are UTC days; a job posted 00:00-02:00 Cairo counts as the day before."""
    if not since or rec.get("status_at"):  # status_at = you pressed 🚀 on Telegram: always apply
        return False
    return rec["posted"][:10] < since[:10] if rec.get("posted") else rec.get("first_seen", "") < since


assert is_old({"posted": "2026-11-12T23:00+00:00"}, "2026-11-13T15:00+00:00")
assert not is_old({"posted": "2026-11-13T01:00+00:00", "first_seen": "2026-11-13T09:00+00:00"}, "2026-11-13T15:00+00:00")
assert is_old({"first_seen": "2026-11-13T09:00+00:00"}, "2026-11-13T15:00+00:00") and not is_old({}, "")
assert not is_old({"first_seen": "2026-11-10T09:00+00:00", "status_at": "2026-11-14T11:00+00:00"}, "2026-11-14T10:32+00:00")


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
    roots = [RECEIPTS / "_shots", Path(tempfile.gettempdir()) / "playwright-mcp-output", CV_DIR]  # agy saves it in its cwd
    shots = [p for r in roots if r.exists() for p in (r.glob if r == CV_DIR else r.rglob)(f"{job_id}*.png") if p.stat().st_mtime >= since]
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
    head = (f"{icon}: <b>{html.escape(rec['title'])}</b>\n🏢 {html.escape(rec['company'])} · ⭐ {rec.get('score', '-')}/10\n"
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
    if status in ("needs_you", "blocked"):  # its tab gets closed: keep where it stopped
        entry["stop_url"] = stop_url() or rec["url"]
    with (RECEIPTS / "index.jsonl").open("a", encoding="utf-8") as f:  # one line per job, for a quick review later
        f.write(json.dumps({"id": rec["id"], "title": rec["title"], "company": rec["company"], "url": rec["url"],
                            **entry}, ensure_ascii=False) + "\n")
    log(f"[{status}] {rec['title']} | {rec['company']} | {note}")
    return entry


SKILL = CV_DIR / ".agents" / "skills" / "job-hunter" / "SKILL.md"
CV_MENU = CV_DIR / "cv_menu.md"
CHANNEL = {"easy_apply": "LinkedIn Easy Apply", "external": "LinkedIn -> company site", "no_button": "LinkedIn",
           "wuzzuf": "Wuzzuf", "bayt": "Bayt", "naukrigulf": "NaukriGulf", "gulftalent": "GulfTalent", "indeed": "Indeed", "external_direct": "company site"}
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
        open_qs, agreed = answers.get("open") or [], {}
        if open_qs and cfg.get("apply", {}).get("sonnet_consult", False):  # second opinion before waiting for you
            facts = rdir / "facts_plus.md"
            facts.write_text(FACTS.read_text(encoding="utf-8") + known_answers(), encoding="utf-8")
            agreed, open_qs, who = review.consult(rec, open_qs, facts)
            log(f"[i] consult ({who}): {len(agreed)} answered from the facts, {len(open_qs)} left for you")
        reply = ask_user(rec, open_qs, env, cfg.get("apply", {}).get("ask_minutes", 30)) if open_qs else {}
        if reply is None:
            qs = " | ".join(str(x.get("q", "")) for x in open_qs)
            return finish(rec, "needs_you", f"أسئلة مش في حقايقك (مافيش رد على Telegram): {qs}"[:600], env, rdir, answers)
        if reply == "skip":
            return finish(rec, "skipped", "انت قلت سيبها (Telegram)", env, rdir, answers)
        given.update(reply)
        remember(reply)  # only yours: the consult's answers still go through the audit + review
        run = agy(ANSWER.format(answers=json.dumps({**agreed, **reply}, ensure_ascii=False), brief=brief),
                  rdir / f"answer{n + 1}",
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
    if (not result.get("submitted") and cfg.get("apply", {}).get("sonnet_submit", False)
            and "captcha" not in str(result.get("reason", "")).lower()):
        log(f"[i] Hydrogen's submit didn't confirm ({str(result.get('reason', 'no receipt'))[:120]}): Sonnet tries")
        result = sonnet_submit(answers, rec["id"], rdir) or result
    (rdir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    if result.get("submitted"):
        append_log(rec, key, answers, result.get("confirmation", ""))
    shot = find_shot(rec["id"], started)
    if result.get("submitted"):
        return finish(rec, "applied", result.get("confirmation", ""), env, rdir, answers, shot, review_note)
    return finish(rec, "needs_you", result.get("reason") or "Submit step didn't confirm.", env, rdir, answers,
                  shot, review_note)


def sonnet_submit(answers, job_id, rdir):
    """The final click by Sonnet (`claude -p`) on the SAME Playwright MCP, so the same job Edge tab.
    Only after Hydrogen's submit didn't confirm and it wasn't a CAPTCHA; the answers were already checked."""
    mcp = RECEIPTS / "_sonnet_mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"playwright": {"type": "http", "url": f"http://localhost:{MCP_PORT}/mcp"}}}))
    prompt = ("The job application below is already filled in the job browser (Playwright MCP). browser_tabs list, "
              "select the tab with the application form (never open, change or close other tabs), then: "
              + SUBMIT.format(answers=json.dumps(answers.get("questions", []), ensure_ascii=False), shot_name=f"{job_id}.png"))
    try:
        out = subprocess.run([review.CLAUDE, "-p", "--model", "claude-sonnet-5-5", "--strict-mcp-config", "--mcp-config",
                              str(mcp), "--allowedTools", "mcp__playwright", "--output-format", "json"], input=prompt,
                             cwd=CV_DIR, creationflags=NO_WINDOW, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=600).stdout
    except subprocess.TimeoutExpired:
        out = ""
    (rdir / "sonnet_submit.json").write_text(out or "", encoding="utf-8")
    run = review.parse_json(out) or {}
    log(f"[i] sonnet submit: {run.get('subtype', 'no result')}, ${run.get('total_cost_usd', 0):.3f}, "
        f"{run.get('num_turns', '?')} turns")
    res = review.parse_json(str(run.get("result") or ""))
    return res if isinstance(res, dict) and "submitted" in res else None


# ---------- main ----------

def main():
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    env = read_json(ENV_FILE) or {}  # only needed for notify_each; the hourly summary comes from GitHub
    env["notify_each"] = cfg.get("apply", {}).get("notify_each", False)
    start_inbox(env)  # your Telegram commands are answered while this runs
    global AGY_MODEL
    AGY_FALLBACK[:] = cfg.get("apply", {}).get("agy_fallback", AGY_FALLBACK)
    AGY_MODEL = review.AGY_MODEL = cfg.get("apply", {}).get("agy_model", AGY_MODEL)
    review.USE_CLAUDE = cfg.get("apply", {}).get("use_claude", True)
    pc = load_pc()
    results = pc.get("results", {})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    results = {k: v for k, v in results.items() if v.get("at", "") >= cutoff}

    if "--jobs" in sys.argv:  # test / plan run: these records instead of the scrapers and the GitHub queue
        queued = read_json(sys.argv[sys.argv.index("--jobs") + 1]) or []
        pc = {"warned": "", **pc, "results": results, "warnings": []}
    else:
        queued = None
    pc = pc if queued is not None else {"jobs": scan(cfg), "results": results, "scanned_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
          "warned": pc.get("warned", ""), "warnings": []}  # warnings show up in the hourly summary
    if queued is None:
        save_pc(pc)  # GitHub gets the new Wuzzuf/Bayt jobs even if applying fails below
        if cfg.get("apply", {}).get("trigger_github", True):
            trigger_github()  # it reads the pc branch we just pushed

    seen = load_seen() if queued is None else {r["id"]: {**r, "status": "queued"} for r in queued}
    if not START_FILE.exists():  # first run: only jobs found from now on (delete the file to reset)
        START_FILE.write_text(datetime.now(timezone.utc).isoformat(timespec="minutes"), encoding="utf-8")
    since = START_FILE.read_text(encoding="utf-8").strip() if queued is None else ""
    skip = words_regex(cfg.get("apply", {}).get("skip_titles"))
    with LOCK:
        requeue = (read_json(REQUEUE) or {}) if queued is None else {}
        # an "apply" stays in the file until its job is done: per_cycle (4) used to drop the rest (5 of 9 on 18 Nov)
        keep = {k: v for k, v in requeue.items() if v == "apply" and k in seen}
        REQUEUE.write_text(json.dumps(keep), encoding="utf-8") if keep else REQUEUE.unlink(missing_ok=True)
    JOBS.update({str(r["n"]): r for r in seen.values() if r.get("n")})
    JOBS.update({r["id"]: r for r in seen.values() if r.get("id")})
    for job_id, what in requeue.items():
        results.pop(job_id, None)
        if what == "skip":
            pc["results"][job_id] = {"at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                                     "status": "skipped", "note": "انت قلت سيبها (Telegram)"}
    again = [{**r, "status": "queued"} for r in seen.values() if requeue.get(r["id"]) == "apply"]
    queue = []
    waiting = sorted((r for r in seen.values() if r.get("status") == "queued" and r["id"] not in results
                      and r["id"] not in requeue), key=lambda r: (region_rank(r), -r.get("score", 0)))
    for rec in again + waiting:  # your late Telegram answers first
        reason = ("تدريب (internship): مش بنقدّم عليه" if skip and skip.search(rec["title"])  # never worth it
                  else "قديمة: اتنشرت قبل يوم تشغيل النظام" if is_old(rec, since)
                  else "موجودة في applications_log.md (اتقدّم عليها أو اتسابت قبل كده)" if already_logged(rec)
                  else None)
        if reason:
            pc["results"][rec["id"]] = {"at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                                        "status": "skipped", "note": reason}
            log(f"[skipped] {rec['title']} | {rec['company']} ({reason})")
            requeue_done(rec["id"])
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
            log(f"[>] applying: {rec['title']} | {rec['company']} | ⭐ {rec.get('score', '-')} | CV: "
                f"{cv_file.name if cv_file else 'from cv_menu.md'}")
            unminimize()
            try:
                pc["results"][rec["id"]] = apply_one(rec, cfg, env, rules, cv_file)
            except Exception as e:  # one job going wrong must not stop the others
                pc["results"][rec["id"]] = finish(rec, "needs_you", f"runner error: {e}", env, RECEIPTS / rec["id"])
            close_tabs()  # nothing left open between jobs (a stopped one: its stop_url reopens it)
            save_pc(pc)  # after every job: a crash never loses a result
            requeue_done(rec["id"])
            if set(read_json(REQUEUE) or {}) - {r["id"] for r in queue} and i + 1 < len(queue):  # "قدم 111" on Telegram: it goes next, not after this cycle
                log("[i] you asked for a job on Telegram: it goes next (the rest of this cycle waits)")
                break


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
            while time.time() < start + every * 60 and not (read_json(REQUEUE) or {}):
                time.sleep(5)  # "قدم 111" on Telegram starts the next run now
    except KeyboardInterrupt:  # subprocess.run kills the running agy / claude on the way out
        log("[i] stopped (Ctrl+C)")


if __name__ == "__main__":
    if "--login" in sys.argv:  # once: sign in to the job sites in the job Edge (it keeps the logins)
        ensure_edge(LOGIN_SITES)
        print("Sign in on each tab of the new Edge window (LinkedIn, Indeed, Wuzzuf, Bayt, Google, NaukriGulf, GulfTalent). Keep it open.")
        sys.exit()
    if "--questions" in sys.argv:  # every question that stopped a job -> receipts/questions_to_answer.md
        print(f"{write_questions()} questions -> {QUESTIONS_FILE}")
        sys.exit()
    if "--import-answers" in sys.argv:
        print(f"{import_answers()} answers saved to {MY_ANSWERS}")
        sys.exit()
    if "--open" in sys.argv:  # reopen a stopped job's page in the job Edge: --open Adria
        print(reopen(" ".join(sys.argv[sys.argv.index("--open") + 1:])))
        sys.exit()
    lock = single_instance()
    if lock is None:
        log("[i] another runner is already working (scheduled task or --live), skipping")
    elif "--live" in sys.argv:
        live()
    else:
        main()
