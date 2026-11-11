"""A CV tailored to one job, made by Claude Sonnet (`claude -p`; Gemini if Claude is out) while Antigravity applies to the job before it.

Sonnet only writes the headline, the profile paragraph and the ORDER of skills lines and projects
(same as every variant in build_cv.py); build_cv.build() turns that into the 2-page PDF.
Any failure -> None, and Antigravity picks a ready CV from cv_menu.md as before.
"""
import html
import json
import re
import sys
from pathlib import Path

import audit
import jobsite
from review import ask

HERE = Path(__file__).resolve().parent
CV_DIR = HERE.parents[2]                        # D:\MY\cv\cloude_CV
OUT_DIR = CV_DIR / "auto_cvs"
sys.path[:0] = [str(HERE.parent), str(CV_DIR)]
from sources import plain                       # noqa: E402

# keys build_cv.reorder() matches inside cv.html (skills labels / project titles)
SKILLS = ["LLMs", "Backend", "Cloud", "Machine Learning", "Data"]
PROJECTS = ["NTEA", "Hieroglyphic", "ID Card", "Face Recognition", "Building Height", "Harmful Algal",
            "Car Counting", "Financial", "LipSync"]

PROMPT = """You tailor Mohamed Nomer's CV to ONE job. Use ONLY facts from candidate_facts.md and the base CV
below: never add a skill, tool, number, title or claim that isn't there (section 9 of the facts lists
what must never be claimed). Write in plain text (no HTML).

Job: {title} | {company} | {location}
<job_description>
{jd}
</job_description>

<candidate_facts>
{facts}
</candidate_facts>

<base_cv>
{cv}
</base_cv>

Reply with JSON only:
{{"title": "<CV headline, max 90 chars, e.g. AI Engineer | LLM Applications, RAG & Agents>",
  "profile": "<profile paragraph, 60-90 words, the job's most important matching facts first>",
  "skills": <these 5 keys, most relevant first: {skills}>,
  "projects": <these 9 keys, most relevant first: {projects}>}}
"""


def job_description(url, limit=4000, html=None):
    """The job's text from its public page (jobsite.page; LinkedIn needs no login). '' if it can't be read."""
    page = jobsite.page(url) if html is None else html
    if not page:
        return ""
    body = re.search(r"show-more-less-html__markup[^>]*>(.*?)</div>", page, re.S)
    if not body:
        page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    return plain(body[1] if body else page, limit=limit)


def check(v, never_claim):
    """Sonnet's JSON -> list of reasons it can't be used ([] = fine)."""
    if not isinstance(v, dict) or not all(isinstance(v.get(k), str) and v[k].strip() for k in ("title", "profile")):
        return ["missing title / profile"]
    why = []
    if sorted(v.get("skills") or []) != sorted(SKILLS):
        why.append(f"skills keys: {v.get('skills')}")
    if sorted(v.get("projects") or []) != sorted(PROJECTS):
        why.append(f"projects keys: {v.get('projects')}")
    if len(v["title"]) > 120 or len(v["profile"].split()) > 120:
        why.append("too long for 2 pages")
    claimed = audit.check([{"q": "cv text", "a": v["title"] + " " + v["profile"]}], {"never_claim": never_claim})
    why += [p["why"] for p in claimed["problems"]]
    return why


def file_name(company):
    # ponytail: one CV per company; two queued jobs at the same company share (and overwrite) it
    return re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_")[:40] or "Job"


def make(rec, facts_file, model="claude-sonnet-5-5", log=print, timeout=600):
    """-> path of the tailored PDF, or None (then the runner falls back to cv_menu.md)."""
    try:
        import build_cv  # D:\MY\cv\cloude_CV\build_cv.py (Edge prints the PDF)
        jd = job_description(rec["url"])
        prompt = PROMPT.format(title=rec["title"], company=rec["company"], location=rec["location"],
                               jd=jd or "(not available: tailor to the job title)",
                               facts=Path(facts_file).read_text(encoding="utf-8"),
                               cv=plain((CV_DIR / "cv.html").read_text(encoding="utf-8"), limit=20000),
                               skills=json.dumps(SKILLS), projects=json.dumps(PROJECTS))
        v, who = ask(prompt, claude_model=model, timeout=timeout)  # Gemini if Claude is out
        why = check(v, audit.load_rules().get("never_claim", []))
        if why:
            log(f"[!] CV for {rec['company']} not used: {'; '.join(why)[:300]}")
            return None
        name = file_name(rec["company"])
        build_cv.build(name, {"title": html.escape(v["title"]), "profile": html.escape(v["profile"]) + " " + build_cv.MSC,
                              "skills": v["skills"], "projects": v["projects"]})
        OUT_DIR.mkdir(exist_ok=True)
        (CV_DIR / f"cv_{name}.html").unlink(missing_ok=True)
        pdf = (CV_DIR / f"Mohamed_Nomer_CV_{name}.pdf").replace(OUT_DIR / f"Mohamed_Nomer_CV_{name}.pdf")
        log(f"[i] tailored CV ready ({who}): {pdf.name} (JD {'read' if jd else 'not found'})")
        return pdf
    except Exception as e:  # never stop an application because of the CV
        log(f"[!] CV for {rec.get('company')} failed: {e}")
        return None
