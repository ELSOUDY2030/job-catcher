"""One-time PC setup: saves the Telegram bot token + your chat id to pc/.env.json (gitignored),
so the PC can send you the application screenshots. The token is typed hidden and never printed.
"""
import getpass
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

ENV_FILE = Path(__file__).with_name(".env.json")


def call(token, method, timeout=20):
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", timeout=timeout) as resp:
        return json.load(resp)["result"]


# GitHub's runs call getUpdates with allowed_updates=["callback_query"], and Telegram REMEMBERS that
# filter: plain getUpdates would then never show your message. So ask for messages explicitly.
MESSAGES = "getUpdates?timeout=10&allowed_updates=" + urllib.parse.quote('["message","callback_query"]')


token = getpass.getpass("Paste the bot token (hidden), then Enter: ").strip()
bot = call(token, "getMe")["username"]
print(f"Bot: @{bot}. Now send any message (e.g. 'pc') to @{bot} in Telegram... waiting up to 2 minutes.")
chat_id = None
for _ in range(12):  # 12 long polls of 10 s = 2 minutes
    chats = [u["message"]["chat"]["id"] for u in call(token, MESSAGES, timeout=30) if "message" in u]
    if chats:
        chat_id = chats[-1]
        break
    time.sleep(1)
if chat_id is None:
    raise SystemExit("No message arrived. Send the bot a message and run this again.")
ENV_FILE.write_text(json.dumps({"token": token, "chat_id": chat_id}), encoding="utf-8")
data = json.dumps({"chat_id": chat_id, "text": "💻 جهازك متوصل بـ Job Catcher. هيقدّم ويبعتلك الإثبات هنا."}).encode()
urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data,
                                              {"Content-Type": "application/json"}), timeout=20).read()
print(f"Saved to {ENV_FILE}. Test message sent.")
