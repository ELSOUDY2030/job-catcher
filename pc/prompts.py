"""What Antigravity (agy -p --agent job-applier) is told. Two steps, so the answers are checked BEFORE Submit."""

FILL = """Read the file {brief} with read_file, in one call (it is your whole task), and follow it exactly.
Your final reply is the JSON receipt it describes, and nothing else."""

# brief.md for one job (pc_runner.write_brief fills it; facts and rules are appended after it)
BRIEF = """# Application brief (your whole task)

Job: {title} | {company} | {location}
Link: {url}
{cv_line}

## Steps: do them in order, nothing else
1. browser_tabs with action "new" and the link above. Work in this tab (and the company tab it opens, if any).
2. Screen the job with the "Screen" rules below. If it isn't a fit: status "skipped" with the reason.
3. Apply with these fixed steps for this site:
{recipe}
4. Stop with status "needs_you" (exact step + error in "reason") on: a login, password, email/SMS code,
   CAPTCHA, account creation or a failed upload.
   A question whose answer is not in the facts (nor in "Answers the user gave before"): fill the rest of that
   page, leave that field empty, and stop with status "ask" and in "open" each such question with your best
   TRUTHFUL suggestion from the facts (free text: 1-3 sentences; never claim what the facts don't say).
   Stay on that page: the user answers on Telegram and you continue from there.
5. Never press the final Submit / Send / Submit application button. Leave the tab on that last page.
6. {snapshot_rule}
7. Tabs, before your final reply: close (browser_tabs action "close") every tab you opened for this job that
   isn't needed. "skipped": close all of them. "ready" / "ask" / "needs_you": keep only the ONE tab with the form,
   on its last page (close the job-board tab the company site was opened from).
   Always also close the extension's connection tab (its URL has "connect.html"): one is left per run otherwise.

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

ANSWER = """The user answered the open questions on Telegram: {answers}
Type or select exactly these answers in those fields (same tab, same page), then continue the steps of {brief}
from there, with the same rules. Never press the final submit.
Final reply: the same JSON receipt ("ask" again only for NEW questions you haven't asked yet)."""

SUBMIT = """Continue the SAME application in the SAME browser tab. Its answers were checked and approved.
1. browser_find the final button ("Submit application" / "Submit" / "Send"). Check that the form still shows
   these answers: {answers}
   If anything differs or a new question appeared, don't submit: reply {{"submitted": false, "reason": "..."}}.
2. Click the final button once and wait for the confirmation.
3. browser_take_screenshot with filename "{shot_name}".
4. If it was submitted: close this tab (browser_tabs action "close"). If not, leave it open for the user.
   Either way, close the extension's connection tab (its URL has "connect.html").
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
Use an empty list if every answer is supported.
"""
