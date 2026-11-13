"""Second opinion on answers the rule check (audit.py) can't verify: salary, free text, unknown questions.
Claude (`claude -p`) first; if Claude can't answer (usage limit, offline, no JSON), Gemini (`agy -p`) does it.
Fails safe: if neither answers, the job is NOT submitted.
"""
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from prompts import CONSULT, REVIEW

CLAUDE = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
AGY = shutil.which("agy") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe")
AGY_MODEL = "gemini-4-hydrogen-medium"  # pc_runner sets it from [apply] agy_model
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "receipts" / "_prompts"  # inside agy's read allow-list


def parse_json(text):
    """First JSON object in the reply, or None if there is none."""
    start = (text or "").find("{")
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text, start)[0]
    except ValueError:
        return None


def parse(text):
    """-> problems list, or None if the reply has no usable JSON."""
    obj = parse_json(text)
    return obj.get("problems") if isinstance(obj, dict) else None


def _run(cmd, timeout, **kw):
    try:
        return subprocess.run(cmd, text=True, encoding="utf-8", capture_output=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kw).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


USE_CLAUDE = True


def ask(prompt, claude_model=None, timeout=300):
    """-> (JSON object, who answered) or (None, reasons). Claude first, Gemini if Claude gives no JSON.
    USE_CLAUDE = False ([apply] use_claude = false): Gemini only, Claude Code's tokens untouched."""
    obj = parse_json(_run([CLAUDE, "-p", "--tools", "", "--output-format", "text"]
                          + (["--model", claude_model] if claude_model else []), timeout, input=prompt)) if USE_CLAUDE else None
    if isinstance(obj, dict):
        return obj, "Claude"
    # the prompt goes in a file: facts + CV are too long for a Windows command line
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    pfile = PROMPTS_DIR / f"{uuid.uuid4().hex}.txt"
    pfile.write_text(prompt, encoding="utf-8")
    try:
        obj = parse_json(_run([AGY, "--model", AGY_MODEL, "--print-timeout", f"{timeout // 60}m", "-p",
                               f"Read the file {pfile} with your file tool and do exactly what it says. "
                               "Use no other tools. Reply with the JSON only."],
                              timeout + 60, stdin=subprocess.DEVNULL, cwd=PROMPTS_DIR.parents[3]))  # D:\MY\cv\cloude_CV
    finally:
        pfile.unlink(missing_ok=True)
    return (obj, "Gemini") if isinstance(obj, dict) else (None, "neither Claude nor Gemini gave JSON")


def consult(record, open_qs, facts_file, timeout=300):
    """Sonnet's second opinion on Hydrogen's open questions, before anyone waits for the user.
    -> ({question: answer} it could support from the facts, questions still for the user, who).
    These answers are NOT saved as yours: the audit + review still check them before the submit."""
    prompt = CONSULT.format(title=record["title"], company=record["company"], location=record["location"],
                            facts=Path(facts_file).read_text(encoding="utf-8"),
                            questions=json.dumps(open_qs, ensure_ascii=False, indent=1))
    obj, who = ask(prompt, claude_model="claude-sonnet-5-5", timeout=timeout)
    got = {str(x.get("q")): str(x["a"]) for x in (obj or {}).get("answers") or []
           if isinstance(x, dict) and x.get("a") not in (None, "", "null")}
    return got, [x for x in open_qs if str(x.get("q", "")) not in got], who


def review(record, unsure, facts_file, timeout=300):
    """-> (problems, who) ([] = all fine). Raises RuntimeError when nobody gave a usable answer."""
    prompt = REVIEW.format(title=record["title"], company=record["company"], location=record["location"],
                           facts=Path(facts_file).read_text(encoding="utf-8"),
                           answers=json.dumps(unsure, ensure_ascii=False, indent=1))
    obj, who = ask(prompt, timeout=timeout)
    if obj is None or not isinstance(obj.get("problems"), list):
        raise RuntimeError(f"review: {who}")
    return obj["problems"], who
