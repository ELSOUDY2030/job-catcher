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
], rules_demo)
assert v["ok"] == 2 and len(v["problems"]) == 3 and len(v["unsure"]) == 2, v
assert {p["why"] for p in v["problems"]} >= {"not outside Egypt"}
assert review.parse('Sure!\n{"problems": []}\nDone') == []
assert review.parse('{"problems": [{"q": "s", "a": "1", "why": "x"}]}')[0]["why"] == "x"
assert review.parse("no json here") is None and review.parse('{"other": 1}') is None
import pc_runner
qa = pc_runner.qa_text([{"q": f"Q{i} <b>", "a": "A"} for i in range(20)], limit=15)
assert qa.count("•") == 15 and "+5" in qa and "&lt;b&gt;" in qa
print("ok")
