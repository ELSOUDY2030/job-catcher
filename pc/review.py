"""Second opinion from Claude (`claude -p`) on answers the rule check (audit.py) can't verify:
salary, free text, unknown questions. Fails safe: if Claude can't answer, the job is NOT submitted.
"""
import json
import shutil
import subprocess
from pathlib import Path

from prompts import REVIEW

CLAUDE = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")


def parse(text):
    """First JSON object in Claude's reply -> problems list, or None if there is none."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text, start)[0].get("problems")
    except (ValueError, AttributeError):
        return None


def review(record, unsure, facts_file, timeout=300):
    """-> list of problems ([] = all fine). Raises RuntimeError when Claude gave no usable answer."""
    prompt = REVIEW.format(title=record["title"], company=record["company"], location=record["location"],
                           facts=Path(facts_file).read_text(encoding="utf-8"),
                           answers=json.dumps(unsure, ensure_ascii=False, indent=1))
    done = subprocess.run([CLAUDE, "-p", "--output-format", "text"], input=prompt, text=True,
                          encoding="utf-8", capture_output=True, timeout=timeout)
    problems = parse(done.stdout)
    if problems is None:
        raise RuntimeError(f"claude -p gave no JSON (exit {done.returncode}): {done.stdout[:200]!r}")
    return problems
