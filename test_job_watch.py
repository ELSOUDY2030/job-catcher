"""Offline self-check (no network). Run: python test_job_watch.py"""
import tomllib
from datetime import date, datetime, timezone

import job_watch
from job_watch import collect, format_job, is_due, job_key, prune, select_new
from sources import clean, make_job, parse_time
from sources.remote_boards import location_ok

UTC = timezone.utc

# --- helpers
assert clean(float("nan")) == "" and clean(None) == "" and clean(" x ") == "x"
assert parse_time(1794144683) == datetime.fromtimestamp(1794144683, UTC)
assert parse_time("2026-10-28T11:00:46.872Z") == datetime(2026, 10, 28, 11, 0, 46, 872000, UTC)
assert parse_time("2026-11-07T01:10:55") == datetime(2026, 11, 7, 1, 10, 55, tzinfo=UTC)
assert parse_time("2026-11-08 10:08:14") == datetime(2026, 11, 8, 10, 8, 14, tzinfo=UTC)
assert parse_time(date(2026, 11, 8)).date() == date(2026, 11, 8)  # end of that day
assert parse_time(float("nan")) is None and parse_time("garbage") is None

# --- same job on two sites -> one key
a = make_job("Senior AI Engineer", "Nawy Real Estate", "", "https://a", None, "linkedin")
b = make_job("senior AI engineer ", "Nawy  Real-Estate", "", "https://b", None, "workable")
assert job_key(a) == job_key(b)
assert job_key(make_job("AI Eng", "Gramian Consulting", "", "u", None, "")) == \
       job_key(make_job("AI Eng", "The Gramian Consulting Group", "", "u", None, ""))
assert job_key(a) != job_key(make_job("Senior AI Engineer", "Valu", "", "u", None, ""))

# --- filtering + dedupe, using the real config
cfg = tomllib.loads(job_watch.CONFIG_FILE.read_text(encoding="utf-8"))
cfg["filter"]["exclude"] = ["intern"]
jobs = [
    a, b,
    make_job("Personal Assistant", "X", "", "https://c", None, "linkedin"),
    make_job("AI Intern", "Y", "", "https://d", None, "bayt"),
    make_job("مهندس ذكاء اصطناعي", "Z", "", "https://e", None, "bayt"),
    make_job("Data Scientist", "Seen Co", "", "https://f", None, "workable"),
    make_job("ML Engineer", "No Url", "", "", None, "workable"),
]
new = select_new(jobs, cfg, seen={job_key(jobs[5]): "2026-11-07"})
assert [j["url"] for j in new.values()] == ["https://a", "https://e"], new

# --- remote location filter
assert location_ok("", cfg) and location_ok("Worldwide", cfg) and location_ok("Americas, EMEA", cfg)
assert location_ok("Spain", cfg) and location_ok("Dubai", cfg)  # Europe + Gulf are in [regions]
assert not location_ok("USA only", cfg) and not location_ok("Almaty, Kazakhstan · remote", cfg)

# --- schedule, age cutoff, one broken source does not stop the rest
now = datetime(2026, 11, 8, 6, 7, tzinfo=UTC)
assert is_due({"every_hours": 6}, now) and not is_due({"every_hours": 6}, now.replace(hour=7))
assert not is_due({"every_hours": 6}, now.replace(minute=37))  # once per due hour, not twice
assert is_due({}, now.replace(minute=37)) and is_due({"every_hours": 1}, now.replace(hour=7, minute=37))
job_watch.SOURCES = {
    "good": lambda s, c: [make_job("AI Eng", "A", "", "u1", "2026-11-08T01:00:00Z", "t"),
                          make_job("AI Eng", "B", "", "u2", "2026-11-01T01:00:00Z", "t")],
    "broken": lambda s, c: 1 / 0,
    "off": lambda s, c: [make_job("AI Eng", "C", "", "u3", None, "t")],
}
got = collect({"hours_old": 24, "off": {"enabled": False}}, now)
assert [j["url"] for j in got] == ["u1"], got

# --- message
msg = format_job(make_job("AI <Engineer>", float("nan"), "Cairo", "https://x/1", "2026-11-08", "linkedin"))
assert "AI &lt;Engineer&gt;" in msg and "nan" not in msg and msg.endswith("https://x/1")

assert prune({"old": {"last_seen": "2026-09-01"}, "new": {"last_seen": "2026-11-07"}}, date(2026, 11, 8)) == \
       {"new": {"last_seen": "2026-11-07"}}
assert parse_time("2026-10-12 15:06:22 UTC") == datetime(2026, 10, 12, 15, 6, 22, tzinfo=UTC)

# --- company boards keep only wanted places
from sources.ats import location_ok as ats_location_ok
from sources import countries_this_run, place_regex, words_regex
places = place_regex(cfg)
for where in ["Cairo Office · Remote", "Egypt · hybrid", "Dubai, United Arab Emirates", "Riyadh onsite",
              "Bucharest, Romania", "London · Remote", "Paris · Zurich", "Global · remote"]:
    assert ats_location_ok(where, places), where
for where in ["", "San Francisco · New York", "Almaty · Kazakhstan · remote", "Toronto Canada", "Bogota remote"]:
    assert not ats_location_ok(where, places), where
assert not words_regex(["oman"]).search("Romania")  # whole words: Oman is not inside Romania

# --- country rotation: Egypt every run, 4 others per run, all covered within a day
regions = cfg["regions"]
rest = regions["gulf"] + regions["arab"] + regions["europe"]
rotate4 = {"regions": {**regions, "per_run": 4}}
runs = [countries_this_run(rotate4, datetime(2026, 11, 10, h, m, tzinfo=UTC)) for h in range(24) for m in (7, 37)]
assert runs[0] != runs[1], "the :07 and :37 runs of one hour must search different countries"
assert all(r[0] == "Egypt" and len(r) == 1 + 4 for r in runs)
assert {c for r in runs for c in r[1:]} == set(rest)
assert countries_this_run({"regions": {"always": ["Egypt"]}}) == ["Egypt"]  # no others configured
everything = countries_this_run({"regions": {**regions, "per_run": 0}})
assert everything[0] == "Egypt" and set(everything[1:]) == set(rest) and len(everything) == 1 + len(rest)
from sources.jobspy_boards import wrong_place
assert wrong_place("Lebanon, PA, USA") and wrong_place("Jordan, MN, United States")
assert not wrong_place("Beirut, Beirut Governorate, Lebanon") and not wrong_place("Amman, Jordan") and not wrong_place("")

# --- telegram: title = first line matching the filter; a post without text is skipped,
#     and does not steal the next post's text
from sources import words_regex
from sources.telegram_channels import parse_channel
page = (
    '<div data-post="chan/1"><div class="tgme_widget_message_text js-message_text">'
    '💼 JOB OPPORTUNITY<br/>🚀 Senior ML Engineer &amp; MLOps<br/>Cairo</div>'
    '<time datetime="2026-11-08T09:00:00+00:00"></time></div>'
    '<div data-post="chan/2"><a class="photo"></a><time datetime="2026-11-08T10:00:00+00:00"></time></div>'
    '<div data-post="chan/3"><div class="tgme_widget_message_text">Course: Excel basics</div>'
    '<time datetime="2026-11-08T11:00:00+00:00"></time></div>'
)
posts = parse_channel(page, "chan", words_regex(cfg["filter"]["include"]))
assert [(p["url"], p["title"]) for p in posts] == [
    ("https://t.me/chan/1", "🚀 Senior ML Engineer & MLOps"),
    ("https://t.me/chan/3", "Course: Excel basics"),
], posts
assert posts[0]["posted"] == datetime(2026, 11, 8, 9, tzinfo=UTC)
# with must_have, the course post (no hiring word) is dropped
must = words_regex(tomllib.loads(job_watch.CONFIG_FILE.read_text(encoding="utf-8"))["telegram_channels"]["must_have"])
assert [p["url"] for p in parse_channel(page, "chan", None, must)] == ["https://t.me/chan/1"]

# --- email alerts (shapes copied from real emails, trimmed)
from email.message import EmailMessage
from sources.email_alerts import message_texts, parse_linkedin, parse_wuzzuf
received = datetime(2026, 11, 8, 1, 13, tzinfo=UTC)
track = "https://x.awstrack.me/L0/https:%2F%2Fwuzzuf.net%2Fjobs%2Fp%2F{}%3Futm_source=wuzzuf%26utm_medium=email/1/abc=473"
wuzzuf_plain = f"""Hi there,
I noticed that you just applied to [*Senior Data Scientist*]({track.format('kfuu-senior-data-scientist-vertex-cairo-egypt')}) at Vertex.

[Junior Data Engineer]({track.format('novmatrjjd3x-junior-data-engineer-vertex-technologies-cairo-egypt')})

*Vertex Technologies - *Cairo, Egypt - Posted 1 day ago

Full Time · Entry Level

[Junior Industrial Application Support Analyst (Outsource)]({track.format('acy2kvxtklf9-junior-industrial-application-support-analyst-outsource-alexandria-egypt')})

*Prometeon tyres group – Egypt - *Alexandria, Egypt - Posted 4 days ago
"""
w = parse_wuzzuf(wuzzuf_plain, received)
assert [(j["title"], j["company"], j["location"]) for j in w] == [
    ("Junior Data Engineer", "Vertex Technologies", "Cairo, Egypt"),
    ("Junior Industrial Application Support Analyst (Outsource)", "Prometeon tyres group – Egypt", "Alexandria, Egypt"),
], w
assert w[0]["url"] == "https://wuzzuf.net/jobs/p/novmatrjjd3x-junior-data-engineer-vertex-technologies-cairo-egypt"
assert w[1]["posted"] == datetime(2026, 11, 4, 1, 13, tzinfo=UTC)

# same email as HTML only -> same jobs (tests the HTML -> lines path)
msg = EmailMessage()
msg.set_content(
    f'<table><tr><td><a href="{track.format("novmatrjjd3x-junior-data-engineer-vertex-technologies-cairo-egypt").replace("&", "&amp;")}">'
    '<b>Junior Data Engineer</b></a></td></tr><tr><td><p><em>Vertex Technologies - </em>Cairo, Egypt - Posted 1 day ago</p>'
    '</td></tr></table>', subtype="html")
w2 = [j for text in message_texts(msg) for j in parse_wuzzuf(text, received)]
assert [(j["title"], j["company"], j["url"]) for j in w2] == [(w[0]["title"], w[0]["company"], w[0]["url"])], w2

linkedin_plain = """Your job alert for computer vision in Sweden
New jobs match your preferences.
Manage alerts: https://www.linkedin.com/comm/jobs/alerts?x=1

Computer Vision Engineer
120ft
Stockholm
View job: https://www.linkedin.com/comm/jobs/view/4487554421/?trackingId=QQ

---------------------------------------------------------

Senior AI Engineer
Saab
Solna

This company is actively hiring
View job: https://www.linkedin.com/comm/jobs/view/4487730499/?trackingId=2D

See all jobs on LinkedIn: https://www.linkedin.com/comm/jobs/search-results/?keywords=cv

New jobs from your other alerts

<strong class="font-bold">data scientist</strong> jobs in Egypt

Senior Data Scientist
BBI
New Cairo

This company is actively hiring
Apply with resume & profile
View job: https://www.linkedin.com/comm/jobs/view/4487724540/?trackingId=gM
"""
li = parse_linkedin(linkedin_plain, received)
assert [(j["title"], j["company"], j["location"], j["url"]) for j in li] == [
    ("Computer Vision Engineer", "120ft", "Stockholm", "https://www.linkedin.com/jobs/view/4487554421"),
    ("Senior AI Engineer", "Saab", "Solna", "https://www.linkedin.com/jobs/view/4487730499"),
    ("Senior Data Scientist", "BBI", "New Cairo", "https://www.linkedin.com/jobs/view/4487724540"),
], li

# --- match score (real config weights)
import scoring
from job_watch import make_record, short_id
import telegram_bot
rules = scoring.rules(cfg)
sc = lambda title, text="": scoring.score(make_job(title, "C", "", "u", None, "t", text=text), rules, cfg)
cv_job = sc("Middle ML Engineer (Computer Vision)", "PyTorch, YOLO, OpenCV, FastAPI, Docker. 3+ years.")
assert cv_job == 10, cv_job
assert sc("Senior LLM Engineer") > sc("AI Engineer") > sc("Data Science Senior Manager, Salesforce") , \
       (sc("Senior LLM Engineer"), sc("AI Engineer"), sc("Data Science Senior Manager, Salesforce"))
assert sc("Head of AI", "10+ years leading teams") == 0
assert sc("ML Engineer", "Java and .NET backend") < sc("ML Engineer"), "negative keywords must lower the score"
assert scoring.years_asked("3+ years of Python, 5-7 years overall") == 3
assert scoring.years_asked("no numbers here") is None
assert words_regex([".net"]).search("C#/.NET developer") and not words_regex(["ml"]).search("HTML")
assert 0 <= sc("") <= 10
# Greenhouse sends HTML escaped once; other sources plain HTML
assert make_job("t", "c", "", "u", None, "s", text=("&lt;p&gt;Hello &amp;amp; bye&lt;/p&gt;", None))["text"] == "Hello & bye"
assert make_job("t", "c", "", "u", None, "s", text="<li>PyTorch</li><li>YOLO</li>")["text"] == "PyTorch YOLO"

# --- buttons: presses change status, only from our chat, unknown ids ignored
now = datetime(2026, 11, 9, 7, 5, tzinfo=UTC)
key = job_key(a)
seen = {key: make_record(key, a, 9, now)}
rid = seen[key]["id"]
assert rid == short_id(key) and len(f"a:{rid}".encode()) <= 64
press = lambda data, chat=42, upd=1: {"update_id": upd, "callback_query": {
    "id": "q", "data": data, "message": {"message_id": 7, "chat": {"id": chat}}}}
done = telegram_bot.apply_presses([press(f"a:{rid}"), press(f"x:{rid}", chat=999), press("a:nope")], 42, seen, now)
assert len(done) == 1 and seen[key]["status"] == "applied"
telegram_bot.apply_presses([press(f"u:{rid}")], "42", seen, now)
assert seen[key]["status"] == "new"
assert telegram_bot.buttons(rid, "applied")["inline_keyboard"][0][0]["callback_data"] == f"u:{rid}"

# --- digest: counts + best open jobs, highest score first
other = make_job("ML Engineer", "Valu", "", "https://v", None, "ashby")
seen[job_key(other)] = make_record(job_key(other), other, 5, now)
seen[job_key(other)]["status"] = "applied"
seen[job_key(other)]["status_at"] = now.isoformat(timespec="minutes")
d = telegram_bot.digest(seen, now)
assert "🆕 2 وظيفة جديدة" in d and "قدمت على 1" in d and "⭐9" in d and "Valu" not in d.split("مقدمتش")[1], d
# --- wuzzuf direct: embedded initialStoreState (shape copied from a live /a/ page, trimmed)
import json as _json
from sources.wuzzuf import _extract_jobs_from_html, _parse_posted_at
store = {"entities": {
    "job": {"collection": {
        "1": {"attributes": {"title": "Senior AI / ML Engineer", "status": "active", "uri": "jobs/p/abc-senior-ai-ml-engineer",
                             "postedAt": "11/09/2026 21:05:00", "description": "<p>Build CV models};\n with PyTorch</p>",
                             "requirements": "<ul><li>YOLO</li></ul>", "keywords": [{"name": "Computer Vision"}],
                             "workExperienceYears": {"min": 3, "max": 5},
                             "location": {"area": {"name": "Abu Rawash"}, "city": {"name": "Giza"}, "country": {"name": "Egypt"}},
                             "workplaceArrangement": {"displayedName": "On-site"}, "insights": {"applied": 25}},
              "relationships": {"company": {"data": {"id": "c1"}}}},
        "2": {"attributes": {"title": "Data Scientist", "slug": "xyz-data-scientist", "hideCompany": True,
                             "postedAt": "bad date"}},                                   # null status = active
        "3": {"attributes": {"title": "Old ML Job", "status": "archived", "uri": "jobs/p/old"}},  # skipped
    }},
    "company": {"collection": {"c1": {"attributes": {"name": "GB Corp"}}}},
}}
page = f'<script>Wuzzuf.initialStoreState = {_json.dumps(store)};\nWuzzuf.other = {{}};\n</script>'
wz = {j["title"]: j for j in _extract_jobs_from_html(page)}
assert sorted(wz) == ["Data Scientist", "Senior AI / ML Engineer"], wz
top = wz["Senior AI / ML Engineer"]
assert top["company"] == "GB Corp" and top["location"] == "Abu Rawash, Giza, Egypt (On-site)"
assert top["url"] == "https://wuzzuf.net/jobs/p/abc-senior-ai-ml-engineer" and top["note"] == "👥 25 متقدم"
assert top["posted"] == datetime(2026, 11, 9, 21, 5, tzinfo=UTC)
assert "PyTorch" in top["text"] and "YOLO" in top["text"] and "3-5 years" in top["text"]
assert wz["Data Scientist"]["company"] == "Confidential Company" and wz["Data Scientist"]["posted"] is None
assert wz["Data Scientist"]["url"] == "https://wuzzuf.net/jobs/p/xyz-data-scientist" and "note" not in wz["Data Scientist"]
assert _extract_jobs_from_html("<html>no state</html>") == [] and _parse_posted_at(None) is None
assert "👥 25 متقدم" in format_job(top, 9)

# --- auto-apply queue: ⭐ >= auto_min_score is queued (🤖), the rest get a 🚀 button
from job_watch import apply_pc_results
now2 = datetime(2026, 11, 10, 20, 7, tzinfo=UTC)
hi, lo = make_job("Senior LLM Engineer", "Acme", "", "u1", None, "t"), make_job("AI Tester", "Beta", "", "u2", None, "t")
rec_hi, rec_lo = make_record(job_key(hi), hi, 9, now2, 8), make_record(job_key(lo), lo, 5, now2, 8)
assert rec_hi["status"] == "queued" and rec_hi["auto"] and rec_lo["status"] == "new" and not rec_lo["auto"]
assert make_record(job_key(hi), hi, 9, now2)["status"] == "new"  # no [apply] -> nothing automatic
assert "text" not in make_record(job_key(hi), hi, 9, now2)  # description kept only for the auto queue
assert "🤖" in format_job(hi, 9, queued=True) and "🤖" not in format_job(lo, 5)
rows = telegram_bot.buttons(rec_lo["id"], "new")["inline_keyboard"]
assert rows[1][0]["callback_data"] == f"q:{rec_lo['id']}"                      # 🚀 = queue it
assert [b["callback_data"][0] for b in telegram_bot.buttons(rec_hi["id"], "queued")["inline_keyboard"][0]] == ["u", "a"]
seen2 = {job_key(hi): rec_hi, job_key(lo): rec_lo}
telegram_bot.apply_presses([press(f"q:{rec_lo['id']}")], 42, seen2, now2)
assert rec_lo["status"] == "queued"
# the PC's results land only on queued jobs; a manual ✅ from you is never overwritten
rec_lo["status"] = "applied"
pc_report = {"results": {rec_hi["id"]: {"status": "applied", "at": "2026-11-10T20:30", "note": "Thanks!"},
                         rec_lo["id"]: {"status": "blocked", "at": "2026-11-10T20:30"}, "zzz": {"status": "applied"}}}
assert apply_pc_results(seen2, pc_report) == 1
assert rec_hi["status"] == "applied" and rec_hi["note"] == "Thanks!" and rec_lo["status"] == "applied"

# --- supervisor engine (made-up rules here; the real ones are private in pc/facts_rules.toml)
# --- hourly summary: one message with everything since the last one
from datetime import timedelta
t0 = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)
since, at = t0.isoformat(timespec="minutes"), (t0 + timedelta(minutes=30)).isoformat(timespec="minutes")
def rec(title, score, status, **extra):
    j = make_job(title, "Co" + title[:3], "Cairo", f"https://x/{title}", None, "t")
    r = make_record(job_key(j), j, score, t0 + timedelta(minutes=10), 8)
    r.update(status=status, **extra)
    return job_key(j), r
seen3 = dict([
    rec("LLM Engineer", 9, "applied", status_at=at),
    rec("Vision Engineer", 9, "needs_you", status_at=at, note="CAPTCHA on the last step"),
    rec("RAG Engineer", 8, "blocked", status_at=at, note="Python 6 -> must be 5"),
    rec("ML Intern", 8, "skipped", status_at=at, note="title is in [apply] skip_titles"),
    rec("Data Scientist", 9, "queued"),
    rec("AI Developer", 7, "new"), rec("NLP Engineer", 6, "new"),
    ("weak|x", {"id": "w1", "status": "low", "score": 2, "first_seen": at, "last_seen": "2026-11-11"}),
    rec("Old Applied", 9, "applied", status_at="2026-11-10T20:00+00:00"),  # before the window: not listed
])
pc_ok = {"scanned_at": at, "warnings": ["جهازك مش قادر يقدّم: Playwright MCP not running"]}
text, markup = telegram_bot.summary(seen3, since, t0 + timedelta(hours=1), cfg, pc_ok)
assert "9:00 ص" in text and "10:00 ص" in text, text                     # Cairo time (UTC+2)
assert "<b>✅ اتقدّم (1)</b>" in text and "LLM Engineer" in text and "Old Applied" not in text
assert "ماتقدّمش على 3" in text and "CAPTCHA on the last step" in text and "Python 6 -&gt; must be 5" in text
assert "⏳ <b>1</b> وظيفة في الطابور" in text and "🔇 1 ضعاف" in text and "Playwright MCP" in text
assert text.index("AI Developer") < text.index("NLP Engineer")             # best first
assert [b["callback_data"][:2] for b in markup["inline_keyboard"][0]] == ["Q:", "Q:"]
stale, _ = telegram_bot.summary(seen3, since, t0 + timedelta(hours=3), cfg, {"scanned_at": at})
assert "جهازك مابعتش حاجة" in stale                                       # PC silent > 90 min
quiet, no_buttons = telegram_bot.summary({}, since, t0 + timedelta(hours=1), cfg, {})
assert "مفيش جديد" in quiet and no_buttons is None
ai_dev = next(r for r in seen3.values() if r.get("title") == "AI Developer")
telegram_bot.apply_presses([press(f"Q:{ai_dev['id']}")], 42, seen3, t0)
assert ai_dev["status"] == "queued"                                        # 🚀 inside the summary works

import sys
sys.path.insert(0, str(job_watch.HERE / "pc"))
import audit
import review
rules_demo = {"never_claim": ["CrewAI", ".NET"], "rule": [
    {"q": "authori[sz]ed to work", "unless": "egypt", "a": "^no\\b", "why": "not outside Egypt"},
    {"q": "authori[sz]ed to work", "a": "^yes\\b", "why": "yes in Egypt"},
    {"q": "(?=.*years)(?=.*python)", "a": "^\\s*5\\b", "why": "Python = 5"},
    {"q": "salary", "review": True}]}
v = audit.check([
    {"q": "Are you legally authorized to work in the UK?", "a": "Yes"},        # problem
    {"q": "Are you authorized to work in Egypt?", "a": "Yes"},                  # ok
    {"q": "Years of experience with Python", "a": "5"},                         # ok
    {"q": "Expected salary", "a": "12345 EGP"},                                 # unsure (review)
    {"q": "Why us?", "a": "I love building with CrewAI"},                       # problem (never_claim)
    {"q": "Why us?", "a": "I build RAG with FastAPI"},                          # unsure (no rule)
    {"q": "Stack?", "a": "C#/.NET"},                                            # problem (never_claim, symbol edge)
    {"q": "Do you have 2+ years of Python?", "a": "Yes"},                        # unsure (yes/no on a value rule)
], rules_demo)
assert v["ok"] == 2 and len(v["problems"]) == 2 and len(v["unsure"]) == 4, v  # never_claim blocks; a rule mismatch -> reviewer
assert {x.get("hint") for x in v["unsure"]} >= {"not outside Egypt"}
assert review.parse('Sure!\n{"problems": []}\nDone') == []
assert review.parse('{"problems": [{"q": "s", "a": "1", "why": "x"}]}')[0]["why"] == "x"
assert review.parse("no json here") is None and review.parse('{"other": 1}') is None
import pc_runner
assert make_job("Principal Data Scientist &#8211; Warsaw", "A &amp; B", "", "u", None, "t")["title"] == \
       "Principal Data Scientist – Warsaw"
skip = words_regex(cfg["apply"]["skip_titles"])
for t in ["CS27 BAC+5 - Stage - Data Scientist (H/F)", "Stage Ingénieur Data/IA", "AI Intern", "Werkstudent ML"]:
    assert skip.search(t), t
for t in ["Founding AI Engineer (early-stage startup)", "Senior ML Engineer", "Internal Tools AI Engineer"]:
    assert not skip.search(t), t
assert sc("CS27 BAC+5 - Stage - Data Scientist (H/F)") < cfg["apply"]["auto_min_score"]
for url in ["https://github.com/ELSOUDY2030/job-catcher.git\n", "https://github.com/ELSOUDY2030/job-catcher",
            "git@github.com:ELSOUDY2030/job-catcher.git"]:
    assert pc_runner.repo_slug(url) == "ELSOUDY2030/job-catcher", url
log_demo = "| 12 | BrainsStem | AI Engineer | Wuzzuf n0x2wacijoq1 | draft |\n| 13 | Valu | Senior Data Scientist (Cairo) | LinkedIn |"
assert pc_runner.already_logged({"url": "https://wuzzuf.net/jobs/p/n0x2wacijoq1-ai-engineer-brainsstem", "title": "x",
                                 "company": "y"}, log_demo)
assert pc_runner.already_logged({"url": "https://x/1", "title": "Senior Data Scientist", "company": "Valu"}, log_demo)
assert not pc_runner.already_logged({"url": "https://www.linkedin.com/jobs/view/4489445391", "title": "AI Engineer",
                                     "company": "Luxoft"}, log_demo)
import tempfile as _tf
import json
import jobsite
from pathlib import Path as _P
assert jobsite.linkedin_kind('x data-tracking-control-name="public_jobs_apply-link-onsite"') == "easy_apply"
assert jobsite.linkedin_kind('<a data-tracking-control-name="public_jobs_apply-link-offsite_x">') == "external"
assert jobsite.linkedin_kind("<html>no button</html>") == "no_button" and jobsite.linkedin_kind("") is None
assert jobsite.recipe_key("https://wuzzuf.net/jobs/p/x") == "wuzzuf"
assert jobsite.recipe_key("https://apply.workable.com/x/j/1") == "external_direct"
assert jobsite.recipe_key("https://se.indeed.com/viewjob?jk=1") == "indeed"
assert set(jobsite.RECIPES) == set(pc_runner.CHANNEL)  # every site has a name in applications_log.md
assert jobsite.recipe_key("https://www.linkedin.com/jobs/view/123", html="") == "no_button"
with _tf.TemporaryDirectory() as _d:  # brief + log row on fake files (no private data, no network)
    _d = _P(_d)
    (_d / "facts.md").write_text("# Facts\nSalary {not a placeholder}\n", encoding="utf-8")
    (_d / "SKILL.md").write_text("# S\n## 2. Screen\nskip rule\n## 3. CV\nno\n## 4. Apply\napply rule\n## 5. Log\n",
                                 encoding="utf-8")
    (_d / "menu.md").write_text("| focus | X.pdf |\n", encoding="utf-8")
    (_d / "log.md").write_text("| 7 | Old | Role | LinkedIn | CV | n |\n- note\n", encoding="utf-8")
    pc_runner.FACTS, pc_runner.SKILL, pc_runner.CV_MENU, pc_runner.APPS_LOG = (
        _d / "facts.md", _d / "SKILL.md", _d / "menu.md", _d / "log.md")
    job_demo = {"title": "AI Eng", "company": "Acme", "location": "Cairo", "score": 9,
                "url": "https://www.linkedin.com/jobs/view/123"}
    for cv in (None, _d / "Mohamed_Nomer_CV_Acme.pdf"):
        for key in jobsite.RECIPES:
            b = pc_runner.write_brief(job_demo, _d, cv, key).read_text(encoding="utf-8")
            assert "skip rule" in b and "apply rule" in b and "no\n" not in b and "{not a placeholder}" in b
            assert ("Mohamed_Nomer_CV_Acme.pdf" in b) == bool(cv) and ("| focus | X.pdf |" in b) != bool(cv)
            assert "{cv_file}" not in b and "{company}" not in b
    pc_runner.append_log(job_demo, "easy_apply", {"cv_file": "A.pdf", "questions": [{"q": "Email|x", "a": "y"}]}, "Sent!")
    pc_runner.append_log(job_demo, "easy_apply", {"cv_file": "A.pdf", "questions": []}, "Sent!")
    log_rows = (_d / "log.md").read_text(encoding="utf-8")
    assert log_rows.count(pc_runner.LOG_SECTION) == 1 and "| 8 | Acme |" in log_rows and "| 9 | Acme |" in log_rows
    assert "Email/x: y" in log_rows and pc_runner.already_logged(job_demo, log_rows)
assert pc_runner.reply_json({"response": 'ok {"status": "ready", "questions": []}'}, "status")["status"] == "ready"
assert pc_runner.reply_json({"response": "no json"}, "status") is None and pc_runner.reply_json({}, "submitted") is None
import cv_maker
cv_ok = {"title": "AI Engineer | Agents", "profile": "Builds LLM apps.", "skills": cv_maker.SKILLS[::-1],
         "projects": cv_maker.PROJECTS}
assert cv_maker.check(cv_ok, ["LangSmith"]) == []
assert cv_maker.check({**cv_ok, "profile": "Uses LangSmith daily"}, ["LangSmith"])  # never-claim word
assert cv_maker.check({**cv_ok, "skills": ["LLMs"]}, []) and cv_maker.check(None, [])
assert cv_maker.file_name("Qatar Insurance Group") == "Qatar_Insurance_Group"
assert sc("ML Engineer", "Strong C# and ASP.NET Core required, Greek residents only") < sc("ML Engineer")
qa = pc_runner.qa_text([{"q": f"Q{i} <b>", "a": "A"} for i in range(20)], limit=15)
assert qa.count("•") == 15 and "+5" in qa and "&lt;b&gt;" in qa
from datetime import datetime as _dt, timezone as _tz
_now = _dt(2026, 11, 11, 12, 7, tzinfo=_tz.utc)
assert job_watch.search_hours(None, _now, 24) == 24                       # first run: full day
assert job_watch.search_hours("2026-11-11T11:37+00:00", _now, 24) == 1   # normal 30-min gap
assert job_watch.search_hours("2026-11-11T09:07+00:00", _now, 24) == 4   # missed runs: bigger window
assert job_watch.search_hours("2026-11-01T09:07+00:00", _now, 24) == 24  # capped

# questions on Telegram: your reply -> answers
assert pc_runner.parse_reply("تمام", 2) == {}
assert pc_runner.parse_reply("2: 4 years\n1- relocation", 2) == {2: "4 years", 1: "relocation"}
assert pc_runner.parse_reply("Growth", 1) == {1: "Growth"} and pc_runner.parse_reply("Growth", 2) is None
assert pc_runner.parse_reply("9: x", 2) is None  # no question 9
assert pc_runner.SCREEN_STEP in pc_runner.BRIEF  # write_brief drops it after Sonnet's screen: must match the template
with _tf.TemporaryDirectory() as d:  # switch_model: 3 "No capacity" lines in one run -> the next model
    f = _P(d) / "x.log"
    f.write_text("No capacity available" + chr(10) + "No capacity available No capacity available")
    model, fallback, real_log = pc_runner.AGY_MODEL, pc_runner.AGY_FALLBACK[:], pc_runner.log
    pc_runner.log = lambda m: None  # not into receipts/runner.log
    pc_runner.AGY_MODEL, pc_runner.AGY_FALLBACK[:] = "a", ["b"]
    pc_runner.switch_model(f)
    assert pc_runner.AGY_MODEL == "b" and not pc_runner.AGY_FALLBACK
    pc_runner.AGY_FALLBACK[:] = ["c"]
    pc_runner.switch_model(_P(d) / "none.log", "Individual quota reached. Resets in 4h")
    assert pc_runner.AGY_MODEL == "c"
    pc_runner.AGY_FALLBACK[:] = ["gemini-4-argon-medium", "claude-sonnet-5-5-low"]  # blocked: never switched to
    pc_runner.switch_model(_P(d) / "none.log", "quota reached")
    assert pc_runner.AGY_MODEL == "c"
    pc_runner.AGY_MODEL, pc_runner.AGY_FALLBACK[:], pc_runner.log = model, fallback, real_log

# every Egypt job is auto-applied whatever its score; elsewhere the threshold decides
cai = {"title": "AI Engineer", "company": "X", "location": "Cairo, Egypt", "url": "https://x/1", "source": "wuzzuf"}
assert make_record(job_key(cai), cai, 2, now2, 6, egypt_any_score=True)["auto"]
assert not make_record(job_key(cai), cai, 2, now2, 6)["auto"]
assert not make_record(job_key(hi), {**cai, "location": "Berlin"}, 5, now2, 6, egypt_any_score=True)["auto"]

# Telegram chat by job number
import tempfile as _tf
import json
import threading as _th
from pathlib import Path as _P
from job_watch import number_jobs
_s = {}
_seen = {"a": {"id": "a1", "first_seen": "2"}, "b": {"id": "b1", "first_seen": "1"}, "c": {"id": "c1", "status": "low"}}
number_jobs(_seen, _s)
assert (_seen["b"]["n"], _seen["a"]["n"], "n" in _seen["c"], _s["next_n"]) == (1, 2, False, 3)
number_jobs(_seen, _s)
assert _s["next_n"] == 3  # numbered once

pc = pc_runner
assert pc.parse_command("قدم 111") == ("apply", "111", "")
assert pc.parse_command("قدّم #111") == ("apply", "111", "")
assert pc.parse_command("اعتمد 111") == ("ok", "111", "")
assert pc.parse_command("اعتمد") == ("ok", None, "")
assert pc.parse_command("111 2: 4 years") == ("edit", "111", "2: 4 years")
assert pc.parse_command("2: 4 years") == ("edit", None, "2: 4 years")
assert pc.parse_command("111") == ("status", "111", "")
assert pc.parse_command("سيب 111") == ("skip", "111", "")
assert pc.parse_command("افتح FlairsTech") == ("open", "FlairsTech", "")
assert pc.parse_command("ازيك")[0] == "help"
assert [pc.parse_command(x)[:2] for x in ("مصر", "الخليج", "السعودية", "ريموت", "اوروبا")] == [
    ("region", "0"), ("region", "1"), ("region", "1"), ("region", "2"), ("region", "3")]
_jobs, _rows = dict(pc.JOBS), pc.index_rows
pc.JOBS.clear()
for _r in [{"id": "e1", "n": 7, "title": "AI Engineer", "company": "Valeo", "location": "Cairo, Egypt", "status": "queued"},
           {"id": "e2", "n": 8, "title": "ML Eng", "company": "IBM", "location": "Giza, Egypt", "status": "queued"},
           {"id": "g1", "n": 9, "title": "DS", "company": "Aramco", "location": "Riyadh, Saudi Arabia", "status": "new"}]:
    pc.JOBS[str(_r["n"])] = pc.JOBS[_r["id"]] = _r
pc.index_rows = lambda: [{"id": "e2", "status": "needs_you", "note": "login wall"}]
_rep = pc.region_report()
assert "مصر: لقى 2" in _rep and "⏳ فاضل 1" in _rep and "✋ محتاجك 1" in _rep and "الخليج: لقى 1" in _rep, _rep
_det = pc.handle(*pc.parse_command("مصر"))
assert "#7 AI Engineer · Valeo" in _det and "login wall" in _det and "Aramco" not in _det, _det
pc.JOBS.clear(); pc.JOBS.update(_jobs); pc.index_rows = _rows

_d = _P(_tf.mkdtemp())
saved = (pc.PENDING, pc.REQUEUE, pc.MY_ANSWERS, pc.RECEIPTS, pc.log)
pc.PENDING, pc.REQUEUE, pc.MY_ANSWERS, pc.RECEIPTS = _d / "p.json", _d / "r.json", _d / "a.json", _d
pc.log = lambda m: None
pc.JOBS.clear()
for r in ({"id": "j1", "n": 111, "title": "AI", "company": "FlairsTech", "url": "u1"},
          {"id": "j2", "n": 112, "title": "ML", "company": "Acme", "url": "u2"}):
    pc.JOBS[str(r["n"])] = pc.JOBS[r["id"]] = r
pc.PENDING.write_text(json.dumps({"j1": {"msg": 7, "open": [{"q": "Years of fine tuning?", "suggested": "2"}]}}))
assert "مافيش أسئلة" in pc.handle("ok", "112", "")                 # nothing asked: just apply
assert "اتحفظت" in pc.handle("edit", None, "1: 3")                 # the only pending job: no number needed
assert json.loads(pc.REQUEUE.read_text()) == {"j2": "apply", "j1": "apply"}
assert json.loads(pc.MY_ANSWERS.read_text()) == {"Years of fine tuning?": "3"}
assert pc.handle("status", "999", "").startswith("مالقيتش")
_reopen, pc.reopen = pc.reopen, lambda q: f"opened {q}"
assert pc.handle(*pc.parse_command("افتح Nawy")) == "opened Nawy"  # not a numbered job: by name, from the receipts
pc.reopen = _reopen
assert "اتسابت" in pc.handle("skip", "flairs", "")                 # by company name
assert json.loads(pc.REQUEUE.read_text())["j1"] == "skip"
pc.requeue_done("j1")  # done jobs leave the requeue one by one; the rest wait for the next cycle
assert json.loads(pc.REQUEUE.read_text()) == {"j2": "apply"}
pc.requeue_done("j2"); pc.requeue_done("nope")
assert not pc.REQUEUE.exists()
pc.queue_again("j1", "skip")  # the lines below expect it back

# a job waiting right now: the inbox hands the answer to ask_user
calls = []
def fake_call(token, method, **kw):
    calls.append(method)
    return {"message_id": 9} if method == "sendMessage" else []
pc.telegram_bot.call, real_call = fake_call, pc.telegram_bot.call
pc.INBOX["thread"] = "test"  # don't start the real thread
env_t = {"token": "t", "chat_id": 5}
qs = [{"q": "Why us?", "suggested": "RAG work"}, {"q": "Years of Python?", "suggested": "5"}]
_th.Timer(0.3, lambda: pc.on_update({"update_id": 1, "message": {"chat": {"id": 5}, "text": "111 2: 4"}}, env_t)).start()
assert pc.ask_user(pc.JOBS["j1"], qs, env_t, 1) == {"Why us?": "RAG work", "Years of Python?": "4"}
_th.Timer(0.3, lambda: pc.on_update({"update_id": 2, "callback_query": {"id": "q", "data": "n:j1",
                                     "message": {"chat": {"id": 5}, "message_id": 9}}}, env_t)).start()
assert pc.ask_user(pc.JOBS["j1"], qs, env_t, 1) == "skip"
assert pc.ask_user(pc.JOBS["j1"], qs, {}, 1) is None  # no Telegram on this PC: needs_you as before
pc.on_update({"update_id": 3, "message": {"chat": {"id": 999}, "text": "قدم 111"}}, env_t)  # another chat: ignored
assert calls.count("sendMessage") == 3  # the 2 question messages + the reply to "111 2: 4"
pc.telegram_bot.call = real_call
pc.INBOX.clear()
pc.JOBS.clear()
pc.PENDING, pc.REQUEUE, pc.MY_ANSWERS, pc.RECEIPTS, pc.log = saved

# a re-applied job's new result lands once; the same result next run changes nothing
r_ = {"k": {"id": "z1", "status": "needs_you", "done_at": "2026-11-16T10:00"}}
assert apply_pc_results(r_, {"results": {"z1": {"status": "needs_you", "at": "2026-11-16T10:00"}}}) == 0
assert apply_pc_results(r_, {"results": {"z1": {"status": "applied", "at": "2026-11-16T12:00"}}}) == 1
assert r_["k"]["status"] == "applied"

# promote: recent 👀 jobs that today's rules would auto-apply
from job_watch import promote
_n = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
_p = {"a": {"status": "new", "score": 6, "first_seen": "2026-11-15T10:00"},
      "b": {"status": "new", "score": 4, "first_seen": "2026-11-15T10:00", "location": "Cairo, Egypt"},
      "c": {"status": "new", "score": 4, "first_seen": "2026-11-15T10:00", "location": "Berlin"},
      "d": {"status": "new", "score": 9, "first_seen": "2026-11-01T10:00"}}
assert promote(_p, _n, 6, True) == 2 and _p["a"]["status"] == _p["b"]["status"] == "queued"
assert _p["c"]["status"] == _p["d"]["status"] == "new"

# questions_to_answer.md -> my_answers.json ("ok" = the suggestion, empty = still open)
_d2 = _P(_tf.mkdtemp())
_saved = (pc_runner.QUESTIONS_FILE, pc_runner.MY_ANSWERS)
pc_runner.QUESTIONS_FILE, pc_runner.MY_ANSWERS = _d2 / "q.md", _d2 / "a.json"
pc_runner.QUESTIONS_FILE.write_text(chr(10).join([
    "# x", "", "### 1. Gender?", "- المقترح: Man", "- إجابتك: ok", "",
    "### 2. Travel %?", "- المقترح: 25%", "- إجابتك: 10%", "", "### 3. Absher title?", "- المقترح: x", "- إجابتك: "]),
    encoding="utf-8")
assert pc_runner.import_answers() == 2
assert json.loads(pc_runner.MY_ANSWERS.read_text(encoding="utf-8")) == {"Gender?": "Man", "Travel %?": "10%"}
pc_runner.QUESTIONS_FILE, pc_runner.MY_ANSWERS = _saved

# NaukriGulf + GulfTalent alert emails (shapes from real emails of 16 Nov / 19 Oct 2026; tokens faked)
from sources import email_alerts as _ea
_rcv = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)
_ng = ('<a href="https://ccs-tracking.naukrigulf.com?data=x&amp;redirect=https%3A%2F%2Fwww.naukrigulf.com%2Fnglogin'
       '%2Fuser%2FmailerLogin%3Fconmailer%3DFAKE%26rUrl%3Dhttps%3A%2F%2Fwww.naukrigulf.com%2Fai-engineer-jobs-in-riyadh'
       '-saudi-arabia-in-lamdax-2-to-5-years-n-cd-1-jid-291026501416%253Futm_medium%253Demail">'
       '<table><tr><td>AI Engineer</td><td>Lamdax</td><td>2 - 5 Years</td><td>Riyadh - Saudi Arabia</td>'
       '<td>28 Oct</td></tr></table></a>'
       '<a href="https://ccs-tracking.naukrigulf.com?redirect=https%3A%2F%2Fwww.naukrigulf.com%2Fx-jid-1"><td>AI Lead</td>'
       '<td>Data Scientist</td><td>crucifer</td><td>2 - 9 Years</td><td>Alexandria - Egypt , Cairo - Egypt</td>'
       '<td>Easy Apply</td><td>2d</td></a><a href="https://www.naukrigulf.com/unsubscribe">Unsubscribe</a>')
_j = _ea.parse_naukrigulf(_ng, _rcv)
assert [(j["title"], j["company"], j["location"]) for j in _j] == [
    ("AI Engineer", "Lamdax", "Riyadh, Saudi Arabia"), ("AI Lead", "Data Scientist", "Alexandria, Egypt; Cairo, Egypt")]
assert _j[0]["url"] == "https://www.naukrigulf.com/ai-engineer-jobs-in-riyadh-saudi-arabia-in-lamdax-2-to-5-years-n-cd-1-jid-291026501416"
assert "FAKE" not in str(_j)  # the auto-login link is never kept
assert _j[0]["posted"].date().isoformat() == "2026-10-28" and _j[1]["posted"].date().isoformat() == "2026-11-14"
_gt = ('<a href="https://www.gulftalent.com/uae/jobs/ml-engineer-628689?utm_source=jobalert&amp;jwt=FAKE.TOKEN">'
       '<p>ML Engineer</p><p>SCC Services Romania</p><p>Dubai, UAE</p><p> 4 days ago </p><img alt="x"><p>Easy Apply</p></a>'
       '<a href="https://www.gulftalent.com/candidates/home?jwt=FAKE">Mohammad</a>')
_j = _ea.parse_gulftalent(_gt, _rcv)
assert [(j["title"], j["company"], j["location"], j["url"]) for j in _j] == [
    ("ML Engineer", "SCC Services Romania", "Dubai, UAE", "https://www.gulftalent.com/uae/jobs/ml-engineer-628689")]
assert jobsite.recipe_key("https://www.gulftalent.com/uae/jobs/ml-engineer-628689") == "gulftalent"
assert jobsite.recipe_key(_ea.parse_naukrigulf(_ng, _rcv)[0]["url"]) == "naukrigulf"

# daily Hydrogen search: only the searched sites' job links, trimmed (no tracking / tokens)
_r = {"jobs": [
    {"title": "AI Engineer", "company": "Lamdax", "location": "Riyadh, Saudi Arabia", "posted": "2026-11-14",
     "url": "https://www.naukrigulf.com/ai-engineer-jobs-in-riyadh-saudi-arabia-in-lamdax-n-cd-1-jid-291026501416?x=1"},
    {"title": "ML Engineer", "company": "SCC", "location": "Dubai, UAE", "posted": "",
     "url": "https://www.gulftalent.com/uae/jobs/ml-engineer-628689?jwt=FAKE"},
    {"title": "Phish", "company": "x", "location": "", "url": "https://evil.example/jid-1"}]}
_s = pc_runner.search_jobs(_r)
assert [(j["source"], j["url"]) for j in _s] == [
    ("naukrigulf", "https://www.naukrigulf.com/ai-engineer-jobs-in-riyadh-saudi-arabia-in-lamdax-n-cd-1-jid-291026501416"),
    ("gulftalent", "https://www.gulftalent.com/uae/jobs/ml-engineer-628689")]
assert pc_runner.agent_search({"enabled": False, "pages": ["x"]}) == []

print("ok")

