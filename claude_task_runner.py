#!/usr/bin/env python3
import os, subprocess, requests, threading
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = str(os.getenv("TELEGRAM_CHAT_ID", ""))
PROJECT   = "/root/ai-caller-env/ai-caller"
CLAUDE    = "/usr/local/bin/claude"
offset    = 0
running_task = False

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send(text):
    try:
        requests.post(f"{API}/sendMessage", json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        print(f"Telegram send error: {e}")

def run_task(task: str):
    global running_task
    running_task = True
    send(f"🤖 <b>Task started:</b>\n<code>{task}</code>\n\n⏳ Working...")

    cmd = f'cd {PROJECT} && /root/.nvm/versions/node/v24.13.1/bin/claude --dangerously-skip-permissions "{task}"'

    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True,
            timeout=600,
            env={**os.environ, "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", "")}
        )
        output = (result.stdout or "") + (result.stderr or "")
        output = output.strip()

        # Trim if too long for Telegram (max 4096 chars)
        if len(output) > 3500:
            output = "..." + output[-3500:]

        if result.returncode == 0:
            send(f"✅ <b>Task complete!</b>\n\n<pre>{output[-2000:] if output else 'Done'}</pre>")
        else:
            send(f"⚠️ <b>Task finished with errors:</b>\n<pre>{output[-2000:]}</pre>")

    except subprocess.TimeoutExpired:
        send("⏰ Task timed out after 10 minutes")
    except Exception as e:
        send(f"❌ Error running task: {e}")
    finally:
        running_task = False

def get_updates():
    global offset
    try:
        r = requests.get(f"{API}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35)
        updates = r.json().get("result", [])
        for u in updates:
            offset = u["update_id"] + 1
            msg    = u.get("message", {})
            chat   = msg.get("chat", {})
            text   = (msg.get("text") or "").strip()

            # Only accept messages from your chat
            if str(chat.get("id")) != CHAT_ID:
                send(f"⛔ Unauthorized access attempt from chat {chat.get('id')}")
                continue

            if text == "/status":
                status = "🔄 Busy with a task" if running_task else "🟢 Ready"
                send(f"<b>Claude Code Runner</b>\nStatus: {status}\nProject: {PROJECT}")

            elif text == "/help":
                send(
                    "🤖 <b>Claude Code Remote Runner</b>\n\n"
                    "<b>Commands:</b>\n"
                    "/task &lt;description&gt; — run a task\n"
                    "/status — check if busy\n"
                    "/deploy — git pull + restart service\n"
                    "/logs — show last 20 service logs\n"
                    "/help — show this message\n\n"
                    "<b>Examples:</b>\n"
                    "<code>/task Add an edit button to campaign card in dashboard.html then git add -A and git commit -m 'feat: edit button' and git push</code>\n\n"
                    "<code>/task Fix the bug where active calls card shows empty</code>"
                )

            elif text == "/deploy":
                send("🚀 Deploying...")
                result = subprocess.run(
                    f"cd {PROJECT} && git pull && systemctl restart ai-caller",
                    shell=True, capture_output=True, text=True
                )
                out = (result.stdout + result.stderr).strip()
                send(f"{'✅' if result.returncode == 0 else '❌'} Deploy result:\n<pre>{out}</pre>")

            elif text == "/logs":
                result = subprocess.run(
                    "journalctl -u ai-caller -n 20 --no-pager",
                    shell=True, capture_output=True, text=True
                )
                send(f"📋 <b>Last 20 logs:</b>\n<pre>{result.stdout[-3000:]}</pre>")

            elif text.startswith("/task "):
                if running_task:
                    send("⏳ Already running a task. Please wait for it to finish.")
                    continue
                task = text[6:].strip()
                if not task:
                    send("❌ Please provide a task description after /task")
                    continue
                t = threading.Thread(target=run_task, args=(task,))
                t.daemon = True
                t.start()

            elif text.startswith("/"):
                send(f"❓ Unknown command: {text}\nSend /help for available commands.")

    except Exception as e:
        print(f"Update error: {e}")

if __name__ == "__main__":
    import time
    print("Claude Code remote runner starting...")
    send("🚀 <b>Claude Code Runner is online!</b>\nSend /help to see available commands.")
    while True:
        get_updates()
        time.sleep(1)
