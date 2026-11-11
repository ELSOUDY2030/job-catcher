"""What Antigravity (agy -p) is told to do. Two steps, so the answers are checked BEFORE Submit."""

FILL = """You are applying to ONE job for Mohamed Nomer, unattended. Nobody is watching: never guess.
Do NOT run terminal / shell commands (they are blocked and stop you). Use only the playwright MCP
browser tools (the user's logged-in Edge) and your file read/write tools on these files in {cv_dir}:
- .agents/skills/job-hunter/SKILL.md : follow its sections 2 (screen) and 4 (apply)
- candidate_facts.md : the ONLY source for every answer (its rules at the top apply)
- cv_menu.md : the CV to use (replaces skill section 3 here)
Already done for you, so SKIP these (they cost minutes): applications_log.md was checked and this
job is not in it, so don't read it; prompt_for_other_ai.md, build_cv.py, ats_score.py and the CV
PDFs: don't open them.

Job: {title} | {company} | {location} | match {score}/10
Link: {url}

1. Open the link in a NEW tab. Screen it (skill section 2). If it is not a fit or closed:
   write the receipt with status "skipped" and the reason, then stop.
2. Pick ONE CV from cv_menu.md by the job's focus (don't build a new one).
3. Fill the application (skill section 4) but DO NOT press the final Submit / Send / Apply button.
   Stop on the last page before it and leave that tab open.
   Big pages (LinkedIn): don't read a long snapshot page by page. Look for the exact text you need
   (the CV file name, the field label) and act on that element. If the site lists saved resumes and
   the chosen CV isn't there, upload it from {cv_dir} with browser_file_upload.
4. Stop with status "needs_you" (put the exact step and link in "reason") if you meet: a login,
   password, email or SMS code, CAPTCHA, account creation, or ANY question whose answer is not
   in candidate_facts.md.
5. Write the receipt file {answers_file} (UTF-8 JSON only, nothing else in the file):
{{"status": "ready" | "needs_you" | "skipped", "reason": "...", "cv_file": "<CV file used>",
  "questions": [{{"q": "<question exactly as shown>", "a": "<answer exactly as typed or selected>"}}]}}
   List EVERY field on the form, also pre-filled ones (name, email, phone, years, yes/no, salary,
   cover letter text...). This receipt is checked before anything is sent.
   A complete example receipt is {example_file} (read it with your file tool if you want one).
   Never search folders with commands: write the receipt directly with your file tool.
"""

SUBMIT = """Continue the SAME application in the SAME browser tab. Its answers were checked and approved.
Do NOT run terminal / shell commands (they are blocked). Browser tools and file read/write only.
1. Make sure the form still shows exactly the answers in {answers_file}. If anything differs or a new
   question appeared, do NOT submit: write {result_file} with "submitted": false and the reason.
2. Press the final Submit / Send / Apply button once and wait for the confirmation.
3. Take a screenshot of the confirmation with the playwright browser_take_screenshot tool,
   filename "{shot_name}".
4. Append the row to applications_log.md as the job-hunter skill says (section 5), with every answer.
5. Write {result_file} (UTF-8 JSON only):
{{"submitted": true | false, "reason": "...", "confirmation": "<confirmation text shown on the page>"}}
Do nothing else.
"""

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
