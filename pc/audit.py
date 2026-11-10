"""Supervisor: checks every answer Antigravity typed into an application, BEFORE it is submitted.

Rules live in pc/facts_rules.toml (your answers from candidate_facts.md: private, never uploaded;
pc/facts_rules.example.toml shows the format). For each question the first rule whose `q` regex
matches (and whose `unless` regex doesn't) decides:
  - answer matches `a`          -> ok
  - answer doesn't match `a`    -> problem (the application is stopped, you get told why)
  - rule has review = true      -> unsure (salary etc.: claude -p reviews it against candidate_facts.md)
  - no rule matches             -> unsure
Any answer containing a `never_claim` word (candidate_facts.md section 9) is a problem.
"""
import re
import tomllib
from pathlib import Path

RULES_FILE = Path(__file__).with_name("facts_rules.toml")


def load_rules(path=RULES_FILE):
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def _has(pattern, text):
    return bool(pattern) and re.search(pattern, text or "", re.IGNORECASE) is not None


def _rule_for(question, rules):
    for rule in rules.get("rule", []):
        if _has(rule["q"], question) and not _has(rule.get("unless"), question):
            return rule
    return None


def check(questions, rules):
    """questions: [{"q": ..., "a": ...}] -> {"problems": [...], "unsure": [...], "ok": n}"""
    problems, unsure, ok = [], [], 0
    never = rules.get("never_claim", [])
    for item in questions:
        q, a = str(item.get("q", "")), str(item.get("a", "")).strip()
        claimed = [w for w in never if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", a, re.IGNORECASE)]
        if claimed:
            problems.append({"q": q, "a": a, "why": f"claims something not in candidate_facts: {', '.join(claimed)}"})
            continue
        rule = _rule_for(q, rules)
        if rule is None or rule.get("review"):
            unsure.append({"q": q, "a": a})
        elif _has(rule["a"], a):
            ok += 1
        else:
            problems.append({"q": q, "a": a, "why": rule.get("why", "doesn't match candidate_facts.md")})
    return {"problems": problems, "unsure": unsure, "ok": ok}
