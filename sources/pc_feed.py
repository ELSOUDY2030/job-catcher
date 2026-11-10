"""Jobs your PC found (Wuzzuf + Bayt block GitHub's servers with 403, a home PC isn't blocked).

pc/pc_runner.py scans them every 30 minutes and leaves them, encrypted, on the "pc" branch;
the workflow decrypts that to pc.json before job_watch.py runs. Missing file = PC off = no jobs.
"""
import json
from pathlib import Path

from . import make_job

ROOT = Path(__file__).resolve().parent.parent


def load(section=None):
    try:
        return json.loads((ROOT / (section or {}).get("file", "pc.json")).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def fetch(section, cfg):
    return [make_job(j.get("title"), j.get("company"), j.get("location"), j.get("url"), j.get("posted"),
                     j.get("source", "pc"), text=j.get("text", ""))
            for j in load(section).get("jobs", [])]
