"""Telegram Bot API: send jobs with ✅/❌ buttons, read button presses, daily digest.

No server: every hourly run reads the presses since last time (getUpdates), so a press
is saved up to an hour later. Telegram keeps unread updates for 24h, plenty for an hourly run.
"""
import html
import json
import urllib.request
from datetime import datetime, timedelta, timezone

# callback_data prefix -> status. "Q" = 🚀 inside the hourly summary (that message's buttons stay as they are)
ACTIONS = {"a": "applied", "x": "ignored", "u": "new", "q": "queued", "Q": "queued"}
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
        from_summary = query.get("data", "").startswith("Q:")
        calls = [("answerCallbackQuery", {"callback_query_id": query["id"],
                                          "text": "🚀 اتضافت للطابور" if from_summary else "اتسجل ✔"})]
        if not from_summary:  # a per-job message: show its new state
            calls.append(("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": query["message"]["message_id"],
                                                     "reply_markup": buttons(rec["id"], rec["status"])}))
        for method, params in calls:
            try:
                call(token, method, **params)
            except Exception as e:  # presses older than a few minutes can't be answered; harmless
                print(f"[i] telegram {method}: {e}")
    print(f"[i] telegram: {len(done)} button presses saved")


def _cairo(iso):
    """'2026-11-11T07:02+00:00' -> '9:02 ص' Cairo time (falls back to UTC+2 without tzdata)."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Africa/Cairo")
    except Exception:
        tz = timezone(timedelta(hours=2))
    t = datetime.fromisoformat(iso).astimezone(tz)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'ص' if t.hour < 12 else 'م'}"


def _link(r):
    return f"<a href=\"{html.escape(r.get('url', ''))}\">{html.escape(r.get('title', ''))}</a> · {html.escape(r.get('company', ''))}"


def summary(seen, since, now, cfg, pc=None, top=5, max_list=12):
    """One message for everything since `since`: what was applied to, what wasn't and why,
    the best jobs left (with 🚀 buttons), and the PC's health. -> (text, reply_markup or None)"""
    recs = list(seen.values())
    found = [r for r in recs if r.get("first_seen", "") >= since]
    changed = lambda status: [r for r in recs if r.get("status") == status and r.get("status_at", "") >= since]
    applied, needs, blocked, skipped = (changed(s) for s in ("applied", "needs_you", "blocked", "skipped"))
    queued = [r for r in recs if r.get("status") == "queued"]
    auto_min = cfg.get("apply", {}).get("auto_min_score", 8)
    best = sorted((r for r in found if r.get("status") == "new"), key=lambda r: -r.get("score", 0))
    n_auto = sum(1 for r in found if r.get("auto"))
    n_low = sum(1 for r in found if r.get("status") == "low")

    def block(title, items, why=False):
        if not items:
            return []
        rows = [f"{i}. {_link(r)}" + (f"\n   ↳ {html.escape(r.get('note', '')[:140])}" if why and r.get("note") else "")
                for i, r in enumerate(items[:max_list], 1)]
        if len(items) > max_list:
            rows.append(f"… و{len(items) - max_list} كمان")
        return ["", f"<b>{title} ({len(items)})</b>", *rows]

    lines = [f"📊 <b>ملخص من {_cairo(since)} لـ {_cairo(now.isoformat(timespec='minutes'))}</b>",
             f"🔎 لقينا <b>{len(found)}</b> وظيفة جديدة: 🤖 {n_auto} للتقديم الأوتوماتيك (⭐ {auto_min}+) · "
             f"👀 {len(best)} مناسبين · 🔇 {n_low} ضعاف اتشالوا"]
    lines += block("✅ اتقدّم", applied, why=False)
    if needs or blocked or skipped:
        lines += ["", f"<b>⛔ ماتقدّمش على {len(needs) + len(blocked) + len(skipped)}، والسبب:</b>"]
        lines += block("✋ محتاجك", needs, why=True)[1:]
        lines += block("🛑 المراجعة وقفته قبل الإرسال", blocked, why=True)[1:]
        lines += block("⏭ اتساب", skipped, why=True)[1:]
    if queued:
        per_hour = 2 * cfg.get("apply", {}).get("per_cycle", 4)
        lines += ["", f"⏳ <b>{len(queued)}</b> وظيفة في الطابور: جهازك بيقدّم لحد {per_hour} في الساعة"]
    if best:
        lines += ["", f"<b>👀 أحسن اللي ماتقدّمناش عليهم (درجتهم أقل من {auto_min})</b>: دوس 🚀 لأي واحدة"]
        lines += [f"{i}. ⭐{r['score']} {_link(r)}" for i, r in enumerate(best[:top], 1)]
    pc = pc or {}
    if pc.get("scanned_at") and now - datetime.fromisoformat(pc["scanned_at"]) > timedelta(minutes=90):
        lines += ["", f"⚠️ جهازك مابعتش حاجة من {_cairo(pc['scanned_at'])}: Wuzzuf و Bayt والتقديم واقفين لحد ما يشتغل"]
    for warning in pc.get("warnings", []):
        lines += [f"⚠️ {html.escape(warning)}"]
    if len(lines) == 2 and not found:
        lines = [lines[0], "مفيش جديد الساعة دي. البوت شغال ✔"]
    markup = {"inline_keyboard": [[{"text": f"🚀 {i}", "callback_data": f"Q:{r['id']}"}
                                   for i, r in enumerate(best[:top], 1)]]} if best else None
    return "\n".join(lines)[:4000], markup


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
