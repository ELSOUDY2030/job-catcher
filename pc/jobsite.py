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


SNAPSHOT_RULE = """Browser rules:
   - Tool arguments, exactly these names (others are rejected):
     browser_find {"text": "..."} | browser_click {"target": "<ref like e88>"} |
     browser_type {"target": "<ref>", "text": "..."} | browser_select_option {"target": "<ref>", "values": ["..."]} |
     browser_fill_form {"fields": [{"target": "<ref>", "name": "<label>", "type": "textbox|checkbox|radio|combobox",
     "value": "..."}]} | browser_file_upload {"paths": ["<full path>"]} | browser_tabs {"action": "list|new|select|close"}
   - Pages are big: never read a whole snapshot page by page. Use browser_find with the exact text you need
     (a button, a field label, the CV file name) and act on that element.
   - Uploading the CV: file inputs are hidden, so never target them. Click the VISIBLE button or area
     ("Upload", "Upload file", "Attach", "Select file", "Choose file", "drag and drop"): a file chooser opens.
     Then call browser_file_upload with only "paths": [the CV's full path].
   - Fill all the text fields you can see on a page with ONE browser_fill_form call (every field's ref + value),
     not one browser_type per field: every extra call makes the next one slower.
   - An action that fails: try it ONE more time, then stop with status "needs_you" and the error.
     Never look for workarounds (no other tools, no JavaScript, no tool documentation files)."""

EXTERNAL = """External application site (Workable, Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Breezy,
BambooHR, Pinpoint, a company form):
a. Find and click the apply button ("Apply", "Apply for this job", "I'm interested").
b. If the site asks to log in, create an account, verify an email/SMS code or solve a CAPTCHA: stop, status
   "needs_you", and say which step.
c. Fill every field from the facts. Upload the CV (see the upload rule).
   Decline non-essential cookies. Untick marketing / newsletter boxes.
d. Stop on the page where the only step left is the final "Submit" / "Submit application" / "Send"."""

RECIPES = {
    "easy_apply": """LinkedIn Easy Apply (the code already checked: this job has an Easy Apply button):
a. On the job page: if it says "Applied", "Application submitted" or "No longer accepting applications",
   stop with status "skipped" and that text.
b. browser_find "Easy Apply" and click it. A dialog opens.
c. On every page of the dialog: fill or fix every field from the facts (pre-filled answers can be wrong).
   Resume page: if {cv_name} is in the list, select it; if not, click "Upload resume" and upload
   {cv_file} (see the upload rule).
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
   fill every field from the facts, upload {cv_file} when asked (see the upload rule), and stop on the page
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
    "indeed": """Indeed (a wizard of several short pages; you are logged in to Indeed in Edge):
a. On the job page: if it says "Applied", "You applied" or the job is closed/expired: status "skipped".
b. "Apply on company site": click it, switch to the new tab with browser_tabs, then follow the external
   site steps below. "Apply now" / "Easy apply": click it and continue here.
c. If Indeed asks to sign in or verify an email/SMS code: stop, status "needs_you".
d. On EACH page: browser_find the field labels of that page, fill them from the facts, then click "Continue".
   Phone country / dropdowns that are not a <select>: click the box, then click the option.
   Resume page: choose "Upload a resume" / "Resume options" > upload, and upload {cv_file} (see the upload rule).
   "Relevant experience" / optional pages: "Continue" (or "Skip") without adding anything.
e. Stop on "Please review your application" (its button is "Submit your application").

""" + EXTERNAL,
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
    if "indeed.com" in url:
        return "indeed"
    return "external_direct"
