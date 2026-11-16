"""What Antigravity (agy -p --agent job-applier) is told. Two steps, so the answers are checked BEFORE Submit."""

FILL = """Read the file {brief} with read_file, in one call (it is your whole task), and follow it exactly.
Your final reply is the JSON receipt it describes, and nothing else."""

# brief.md for one job (pc_runner.write_brief fills it; facts and rules are appended after it)
BRIEF = """# Application brief (your whole task)

Job: {title} | {company} | {location}
Link: {url}
{cv_line}

## Steps: do them in order, nothing else
1. browser_tabs with action "list". If a tab of THIS job is already open (the link above, or its application
   form on the company site), select it and continue there. Only if there is none: browser_tabs with action
   "new" and the link above. Never open a second tab for the same page.
2. Screen the job with the "Screen" rules below. If it isn't a fit: status "skipped" with the reason.
3. Apply with these fixed steps for this site:
{recipe}
4. Stop with status "needs_you" (exact step + error in "reason") on: a password form, email/SMS code,
   CAPTCHA or a failed upload. ("Sign in / Continue with Google" and "Apply with LinkedIn" are fine: use them.)
   Soft free-text questions (why this company / role, what excites you, your favorite feature or product, what
   you'd improve, anything else to add, a short cover note): don't stop for them. Write 1-3 plain sentences from
   the job description and the facts: general and honest, never claiming you used their product, never adding
   skills, years or results the facts don't have. List them in "questions" like any other answer (checked later).
   A question whose answer is not in the facts (nor in "Answers the user gave before"): fill the rest of that
   page, leave that field empty, and stop with status "ask" and in "open" each such question with your best
   TRUTHFUL suggestion from the facts (free text: 1-3 sentences; never claim what the facts don't say).
   Stay on that page: the user answers on Telegram and you continue from there.
5. Never press the final Submit / Send / Submit application button. Leave the tab on that last page.
6. {snapshot_rule}
7. Tabs, before your final reply: close (browser_tabs action "close") every tab you opened for this job that
   isn't needed. "skipped": close all of them. "ready" / "ask" / "needs_you": keep only the ONE tab with the form,
   on its last page (close the job-board tab the company site was opened from).

## Your final reply: this JSON only
{{"status": "ready" | "ask" | "needs_you" | "skipped", "reason": "...", "cv_file": "<CV file name used>",
 "questions": [{{"q": "<question exactly as shown>", "a": "<answer exactly as typed or selected>"}}],
 "open": [{{"q": "<question exactly as shown>", "options": ["<choices, if it has any>"], "suggested": "..."}}]}}
"questions" lists EVERY field of the form, pre-filled ones too (name, email, phone, years, yes/no, salary,
cover letter...). It is checked before anything is sent.
"""

RESUME = """Your run ended before you replied with the receipt (shell commands don't exist for you; maybe you
tried one). Continue the SAME application in the SAME tab from where you stopped, using only the playwright
browser tools, following {brief}. Never press the final submit. Final reply: the JSON receipt only."""

CONSULT = """You are the second opinion on a job application form ({title} at {company}, {location}).
The form-filling agent stopped on questions it couldn't answer from the candidate's facts, each with its suggestion.
Answer a question ONLY if the facts below (or "Answers the user gave before") support it directly, or it is a soft
question (why this company, favorite feature, motivation): then 1-3 general, honest sentences that claim no product
use, skill, year or result the facts don't have. A salary question: the facts' expectation for that country, with
currency and period. A question with fixed choices where the fact sits between them (facts: 2 weeks notice;
choices: Immediately / 1 month): pick the CLOSEST choice, on a tie the safer one (later start, lower claim), and
say so in "why"; these general questions shouldn't stop the application. Otherwise (years of a skill the facts don't state, personal data, passport/ID numbers,
anything you'd have to guess) answer null: the user decides those. Never invent.

## Candidate facts
{facts}

## Open questions (JSON)
{questions}

Reply with JSON only: {{"answers": [{{"q": "<question exactly as given>", "a": "<answer, or null>", "why": "<fact used>"}}]}}"""

ANSWER = """The open questions are answered (by the user, or from the facts): {answers}
Type or select exactly these answers in those fields (same tab, same page), then continue the steps of {brief}
from there, with the same rules. Never press the final submit.
Final reply: the same JSON receipt ("ask" again only for NEW questions you haven't asked yet)."""

# a once-a-day search of sites that block scripts, by Hydrogen in the job Edge (pc_runner.agent_search)
SEARCH = """Read the file {brief} with read_file, in one call, and follow it exactly.
Your final reply is the JSON it describes, and nothing else."""

SEARCH_BRIEF = """# Job search brief (your whole task). Today is {today}.
Open each page below in ONE new tab (browser_tabs "new" once, then browser_navigate for the next pages).
On each page: browser_snapshot once, then list every job card shown on that first page of results.
Don't open the jobs, don't scroll for more, don't log in, don't apply, don't click anything else.
If a page shows a login wall, a CAPTCHA or "Access Denied": skip it and go to the next.
When done, close the tab (browser_tabs "close").

Pages:
{pages}

Final reply, JSON only:
{{"jobs": [{{"title": "...", "company": "...", "location": "City, Country", "url": "<the job's own link, full https>",
           "posted": "<YYYY-MM-DD: today minus what the card says ('2d', '1 Nov', '3 days ago'); empty if not shown>"}}]}}
"""

SUBMIT = """Continue the SAME application in the SAME browser tab. Its answers were checked and approved.
1. browser_find the final button ("Submit application" / "Submit" / "Send"). Check that the form still shows
   these answers: {answers}
   If anything differs or a new question appeared, don't submit: reply {{"submitted": false, "reason": "..."}}.
2. Click the final button once and wait for the confirmation. If the form refuses because a REQUIRED consent
   checkbox is unticked (privacy notice, keep my CV on file, career emails: marked * / "This field is required"),
   tick those required boxes only and click once more. A CAPTCHA ("I'm not a robot", "captcha is invalid",
   an image challenge): never solve it; reply submitted false with reason "captcha".
3. browser_take_screenshot with filename "{shot_name}".
4. If it was submitted: close this tab (browser_tabs action "close"). If not, leave it open for the user.
5. Final reply, JSON only:
{{"submitted": true | false, "reason": "...", "confirmation": "<confirmation text shown on the page>"}}"""

REVIEW = """You are a strict reviewer of a job application that an AI agent filled for Mohamed Nomer.
The ONLY source of truth is candidate_facts.md below. For each answer listed, decide whether it is
fully supported by those facts. Salary answers must follow section 7 for this job's country
(and currency). Free-text answers must not claim anything that is not in the facts.

Job: {title} | {company} | {location}

<candidate_facts>
{facts}
</candidate_facts>

<answers_to_check>
{answers}
</answers_to_check>

Reply with JSON only, no other text:
{{"problems": [{{"q": "<question>", "a": "<answer>", "why": "<what is wrong, citing the facts section>"}}]}}
Use an empty list if every answer is supported. List ONLY answers that contradict the facts or claim
something the facts don't have: never an answer the facts confirm (e.g. "Man" when the facts say Male,
"requires visa sponsorship" when the facts say not authorized outside Egypt). "I prefer not to answer" on a
diversity / demographic question is always fine.
"""
