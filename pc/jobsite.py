"""What code can know about a job before any AI touches it, and the fixed steps (recipe) for its site.

LinkedIn's public job page (no login) tells: the description, and the apply type:
  "easy_apply" (apply-link-onsite), "external" (apply-link-offsite), "no_button" (none shown).
"""
import re
import urllib.request

LINKEDIN_ID = re.compile(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d+)")


def page(url):
    """HTML of the job's public page ('' if it can't be read). LinkedIn: the guest API, no login."""
    m = LINKEDIN_ID.search(url)
    src = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m[1]}" if m else url
    try:
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    except Exception:
        return ""


def linkedin_kind(html):
    if "public_jobs_apply-link-onsite" in html:
        return "easy_apply"
    if "public_jobs_apply-link-offsite" in html:
        return "external"
    return "no_button" if html else None


SNAPSHOT_RULE = ("Pages are big: never read a whole snapshot page by page. Use browser_find with the exact text "
                 "you need (a button, a field label, the CV file name) and act on that element.")

EXTERNAL = """External application site (Workable, Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Breezy,
BambooHR, Pinpoint, a company form):
a. Find and click the apply button ("Apply", "Apply for this job", "I'm interested").
b. If the site asks to log in, create an account, verify an email/SMS code or solve a CAPTCHA: stop, status
   "needs_you", and say which step.
c. Fill every field from the facts. Upload the CV with browser_file_upload on the resume/CV file input.
   Decline non-essential cookies. Untick marketing / newsletter boxes.
d. Stop on the page where the only step left is the final "Submit" / "Submit application" / "Send"."""

RECIPES = {
    "easy_apply": """LinkedIn Easy Apply (the code already checked: this job has an Easy Apply button):
a. On the job page: if it says "Applied", "Application submitted" or "No longer accepting applications",
   stop with status "skipped" and that text.
b. browser_find "Easy Apply" and click it. A dialog opens.
c. On every page of the dialog: fill or fix every field from the facts (pre-filled answers can be wrong).
   Resume page: if {cv_name} is in the list, select it; if not, click "Upload resume" and
   browser_file_upload {cv_file}. If an upload fails, stop with status "needs_you" and the error.
   Replace any old cover letter written for another company.
d. Click "Next" / "Review" until the page whose button is "Submit application". Untick "Follow {company}".
e. Stop there.""",
    "external": """LinkedIn job that applies on the company's own site (the code already checked):
a. On the job page: if it says "Applied" or "No longer accepting applications", stop with status "skipped".
b. browser_find "Apply" and click it; the company site opens in a new tab: switch to it with browser_tabs.
c. Then follow the external site steps below.

""" + EXTERNAL,
    "no_button": """LinkedIn job whose public page shows no apply button (often closed):
a. On the job page: if it says "No longer accepting applications", "Applied" or shows no apply button, stop with
   status "skipped" and what it says.
b. Otherwise click the "Easy Apply" or "Apply" button that is there and continue like a normal application:
   fill every field from the facts, upload {cv_file} with browser_file_upload when asked, and stop on the page
   whose only step left is the final submit.""",
    "wuzzuf": """Wuzzuf:
a. If the page shows "Log in" / "Sign up" instead of your account: stop with status "needs_you" (log in to
   Wuzzuf in Edge).
b. If it says "Applied" or the job is closed: status "skipped".
c. Click "Apply For Job", answer every question from the facts, and stop before the final submit button.""",
    "bayt": """Bayt:
a. If it asks to log in or register: stop with status "needs_you" (log in to Bayt in Edge).
b. If it says "Applied" or the job is closed: status "skipped".
c. Click "Apply", answer every question from the facts, attach {cv_file} if asked, and stop before the final
   submit button.""",
    "external_direct": EXTERNAL,
}


def recipe_key(url, html=None):
    """-> key of RECIPES for this job. html: the LinkedIn public page if already fetched."""
    if LINKEDIN_ID.search(url):
        return linkedin_kind(page(url) if html is None else html) or "no_button"
    if "wuzzuf.net" in url:
        return "wuzzuf"
    if "bayt.com" in url:
        return "bayt"
    return "external_direct"
