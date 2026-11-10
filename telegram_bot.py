"""Telegram Bot API: send jobs with ✅/❌ buttons, read button presses, daily digest.

No server: every hourly run reads the presses since last time (getUpdates), so a press
is saved up to an hour later. Telegram keeps unread updates for 24h, plenty for an hourly run.
"""
import html
import json
import urllib.request
from datetime import timedelta

ACTIONS = {"a": "applied", "x": "ignored", "u": "new", "q": "queued"}  # callback_data prefix -> status
LABELS = {"applied": "✅ قدمت", "ignored": "❌ مش مهتم", "queued": "🚀 في الطابور", "needs_you": "✋ محتاجك",
          "blocked": "⛔ اتوقف", "skipped": "⏭ اتساب"}


def call(token, method, **params):
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
                                 json.dumps(params).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read())
    if not body.get("ok"):
        raise RuntimeError(body.get("description"))
    return body["result"]


def buttons(job_id, status="new"):
    if status == "new":
        return {"inline_keyboard": [
            [{"text": LABELS["applied"], "callback_data": f"a:{job_id}"},
             {"text": LABELS["ignored"], "callback_data": f"x:{job_id}"}],
            [{"text": "🚀 قدّم لي", "callback_data": f"q:{job_id}"}],  # your PC applies (pc/pc_runner.py)
        ]}
    if status == "queued":
        return {"inline_keyboard": [
            [{"text": "⏸ ماتقدّمش", "callback_data": f"u:{job_id}"},
             {"text": "✅ قدمت بنفسي", "callback_data": f"a:{job_id}"}],
        ]}
    return {"inline_keyboard": [[{"text": f"{LABELS.get(status, status)} · تراجع ↩️", "callback_data": f"u:{job_id}"}]]}


def send(token, chat_id, text, markup=None):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if markup:
        params["reply_markup"] = markup
    return call(token, "sendMessage", **params)["message_id"]


def apply_presses(updates, chat_id, seen, now):
    """Button presses -> job status. Returns [(callback_query, record)] to acknowledge. No network."""
    by_id = {rec["id"]: rec for rec in seen.values()}
    done = []
    for update in updates:
        query = update.get("callback_query")
        if not query or str(query.get("message", {}).get("chat", {}).get("id")) != str(chat_id):
            continue  # only presses in our own chat
        action, _, job_id = (query.get("data") or "").partition(":")
        rec = by_id.get(job_id)
        if action in ACTIONS and rec:
            rec["status"] = ACTIONS[action]
            rec["status_at"] = now.isoformat(timespec="minutes")
            done.append((query, rec))
    return done


def sync_presses(token, chat_id, state, seen, now):
    updates = call(token, "getUpdates", offset=state.get("offset", 0), timeout=0, allowed_updates=["callback_query"])
    if updates:
        state["offset"] = updates[-1]["update_id"] + 1
    done = apply_presses(updates, chat_id, seen, now)
    for query, rec in done:
        for method, params in (
            ("answerCallbackQuery", {"callback_query_id": query["id"], "text": "اتسجل ✔"}),
            ("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": query["message"]["message_id"],
                                        "reply_markup": buttons(rec["id"], rec["status"])}),
        ):
            try:
                call(token, method, **params)
            except Exception as e:  # presses older than a few minutes can't be answered; harmless
                print(f"[i] telegram {method}: {e}")
    print(f"[i] telegram: {len(done)} button presses saved")


def digest(seen, now, top=5):
    """Daily summary: new in the last 24h, applied in the last 24h, best open jobs."""
    since = (now - timedelta(days=1)).isoformat(timespec="minutes")
    new = [r for r in seen.values() if r.get("first_seen", "") >= since]
    applied = [r for r in seen.values() if r.get("status") == "applied" and r.get("status_at", "") >= since]
    best = sorted((r for r in seen.values() if r.get("status") == "new"), key=lambda r: -r.get("score", 0))[:top]
    e = html.escape
    lines = ["📊 <b>ملخص آخر 24 ساعة</b>",
             f"🆕 {len(new)} وظيفة جديدة  ·  ✅ قدمت على {len(applied)}", "",
             "<b>أحسن وظايف لسه مقدمتش عليها:</b>"]
    lines += [f"⭐{r.get('score', 0)} <a href=\"{e(r['url'])}\">{e(r['title'])}</a> · {e(r['company'])}"
              for r in best] or ["مفيش 🎉"]
    return "\n".join(lines)
