"""
reminder.py - Escalating sarcasm reminder engine for Focus CLI
Checks overdue tasks and fires reminders via Twilio (SMS or WhatsApp)

Usage:
  python3 reminder.py --once     # Check now and exit
  python3 reminder.py            # Run as daemon
"""
import sys
import time
import yaml
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path

import memory
import llm

CONFIG_PATH = Path(__file__).parent / "config.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [reminder] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("reminder")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Quiet Hours ────────────────────────────────────────────────────────────

def is_quiet_hours(config):
    """Don't fire reminders during sleeping hours."""
    now_hour = datetime.now().hour
    start = config["reminders"]["quiet_hours_start"]
    end = config["reminders"]["quiet_hours_end"]

    if start > end:  # Spans midnight e.g. 23–8
        return now_hour >= start or now_hour < end
    return start <= now_hour < end


# ── Sarcasm Tiers ──────────────────────────────────────────────────────────

def get_sarcasm_tier(hours_overdue, escalation_hours):
    """Return tier 1-4 based on how long task has been overdue."""
    if hours_overdue < escalation_hours[0]:
        return 1
    elif hours_overdue < escalation_hours[1]:
        return 2
    elif hours_overdue < escalation_hours[2]:
        return 3
    else:
        return 4


TIER_PROMPTS = {
    1: """Generate a gentle, warm reminder about this overdue task. 
One sentence. Friendly nudge, no sarcasm yet.
Task: {task}
Hours overdue: {hours:.0f}""",

    2: """Generate a reminder about this task that's been sitting untouched for a while.
Warm but noticeably pointed. Light sarcasm okay. One or two sentences.
Don't be mean, just... knowing.
Task: {task}
Hours overdue: {hours:.0f}""",

    3: """Generate a clearly sarcastic but still affectionate reminder.
The tone is like a best friend who's losing patience but still loves you.
Reference how long it's been. Call it out directly. 2 sentences max.
Task: {task}
Hours overdue: {hours:.0f}""",

    4: """Generate a reminder that is fully, lovingly exasperated.
This task has been ignored for days. Be dramatic about it.
Still warm underneath — you want them to succeed — but do NOT hold back the sarcasm.
End with a genuine offer to help them just START, even for 2 minutes.
Task: {task}
Hours overdue: {hours:.0f}"""
}

# Fallback messages if LLM is unavailable
TIER_FALLBACKS = {
    1: "Hey, just a gentle nudge — you wanted to do this today: {task}",
    2: "Still waiting on this one... '{task}'. Just checking in. No rush. (There is a little rush.)",
    3: "Okay so '{task}' has been sitting there for {hours:.0f} hours. We both know what's happening here.",
    4: "'{task}'. {hours:.0f} hours. I'm not angry, I'm just... look, do you want to talk about it? Two minutes. Just start."
}


def generate_reminder_message(task_text, hours_overdue, tier):
    """Generate a sarcasm-calibrated reminder using TinyLlama."""
    prompt = TIER_PROMPTS[tier].format(task=task_text, hours=hours_overdue)

    try:
        message = llm.chat(
            [{"role": "user", "content": prompt}],
            system="You are MissTao, an ADHD assistant. Generate reminder messages exactly as instructed. No preamble, just the message."
        )
        # Clean up any quotes TinyLlama might wrap it in
        message = message.strip().strip('"').strip("'")
        return message
    except Exception:
        fallback = TIER_FALLBACKS[tier]
        return fallback.format(task=task_text, hours=hours_overdue)


# ── Twilio Notifier ────────────────────────────────────────────────────────

def send_notification(message, tier, config):
    """Send via Twilio — SMS or WhatsApp depending on config."""
    try:
        from twilio.rest import Client
    except ImportError:
        log.error("Twilio not installed. Run: pip install twilio --break-system-packages")
        return False

    twilio_cfg = config["twilio"]
    client = Client(twilio_cfg["account_sid"], twilio_cfg["auth_token"])

    use_whatsapp = twilio_cfg.get("use_whatsapp", False)

    if use_whatsapp:
        from_num = f"whatsapp:{twilio_cfg['from_number']}"
        to_num = f"whatsapp:{twilio_cfg['to_number']}"
    else:
        from_num = twilio_cfg["from_number"]
        to_num = twilio_cfg["to_number"]

    try:
        msg = client.messages.create(
            body=message,
            from_=from_num,
            to=to_num
        )
        log.info(f"Sent tier-{tier} reminder via {'WhatsApp' if use_whatsapp else 'SMS'}: {msg.sid}")
        return True
    except Exception as e:
        log.error(f"Twilio send failed: {e}")
        return False


# ── Reminder State ─────────────────────────────────────────────────────────

def get_last_reminded(task_id):
    """Check when we last sent a reminder for this task."""
    with memory.get_conn() as conn:
        row = conn.execute(
            "SELECT last_reminded_at, reminder_tier FROM tasks WHERE id=?",
            (task_id,)
        ).fetchone()
    if row and row[0]:
        return row[0], row[1] or 0
    return None, 0


def mark_reminded(task_id, tier):
    """Record that we just sent a reminder."""
    now = datetime.now().isoformat()
    with memory.get_conn() as conn:
        # Add columns if they don't exist yet (graceful migration)
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN last_reminded_at TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN reminder_tier INTEGER DEFAULT 0")
        except Exception:
            pass
        conn.execute(
            "UPDATE tasks SET last_reminded_at=?, reminder_tier=? WHERE id=?",
            (now, tier, task_id)
        )


def hours_since(iso_timestamp):
    """Calculate hours since an ISO timestamp."""
    if not iso_timestamp:
        return 0
    try:
        then = datetime.fromisoformat(iso_timestamp)
        delta = datetime.now() - then
        return delta.total_seconds() / 3600
    except Exception:
        return 0


# ── Main Check ─────────────────────────────────────────────────────────────

def run_reminder_check(config):
    """Check all active tasks and fire reminders where appropriate."""
    if is_quiet_hours(config):
        log.info("Quiet hours — skipping reminder check")
        return

    tasks = memory.get_active_tasks()
    if not tasks:
        return

    escalation_hours = config["reminders"]["escalation_hours"]
    reminders_sent = 0

    for task in tasks:
        task_id = task["id"]
        task_text = task["text"]

        # Skip appointments (they have their own timing)
        # We'll remind about them separately based on event time
        if task_text.startswith("[APPOINTMENT]"):
            continue

        # Skip tasks auto-extracted from WhatsApp — these may not be yours.
        # To get reminders for a WhatsApp task, edit it to remove the [WhatsApp] tag.
        if "[WhatsApp]" in task_text:
            continue

        last_reminded, last_tier = get_last_reminded(task_id)
        hours_since_created = hours_since(
            memory.get_conn().execute(
                "SELECT created_at FROM tasks WHERE id=?", (task_id,)
            ).fetchone()[0]
        )
        hours_since_last_reminder = hours_since(last_reminded) if last_reminded else hours_since_created

        # Calculate current tier
        current_tier = get_sarcasm_tier(hours_since_created, escalation_hours)

        # Only remind if:
        # 1. We haven't reminded yet, OR
        # 2. Enough time has passed since last reminder (based on tier)
        min_gap_hours = {1: 2, 2: 4, 3: 8, 4: 12}
        min_gap = min_gap_hours.get(current_tier, 4)

        should_remind = (
            last_reminded is None or
            (hours_since_last_reminder >= min_gap and current_tier > last_tier)
        )

        if not should_remind:
            continue

        log.info(f"Task [{task_id}] '{task_text[:40]}...' — tier {current_tier}, {hours_since_created:.1f}h old")

        message = generate_reminder_message(task_text, hours_since_created, current_tier)
        log.info(f"  Message: {message[:80]}...")

        sent = send_notification(message, current_tier, config)
        if sent:
            mark_reminded(task_id, current_tier)
            reminders_sent += 1

    log.info(f"Reminder check complete — {reminders_sent} reminders sent")


# ── Appointment Reminders ──────────────────────────────────────────────────

def run_appointment_check(config):
    """Fire reminders for upcoming appointments."""
    tasks = memory.get_active_tasks()
    appointment_tasks = [t for t in tasks if t["text"].startswith("[APPOINTMENT]")]

    for task in appointment_tasks:
        # Try to extract time from task text
        # Format: [APPOINTMENT] Title — Mon Jan 01 at 14:00 @ Location
        import re
        match = re.search(r'(\w{3} \w{3} \d+ at \d+:\d+)', task["text"])
        if not match:
            continue

        try:
            event_time_str = match.group(1)
            event_time = datetime.strptime(
                f"{datetime.now().year} {event_time_str}",
                "%Y %a %b %d at %H:%M"
            )
            hours_until = (event_time - datetime.now()).total_seconds() / 3600

            last_reminded, _ = get_last_reminded(task["id"])

            # Remind at 24h before and 1h before
            if 23 <= hours_until <= 25 and not last_reminded:
                msg = f"📅 Tomorrow: {task['text'].replace('[APPOINTMENT] ', '')} — don't forget to prepare!"
                send_notification(msg, 1, config)
                mark_reminded(task["id"], 1)

            elif 0.5 <= hours_until <= 1.5 and (not last_reminded or hours_since(last_reminded) > 20):
                msg = f"⏰ Starting in ~1 hour: {task['text'].replace('[APPOINTMENT] ', '')}"
                send_notification(msg, 1, config)
                mark_reminded(task["id"], 2)

        except Exception as e:
            log.debug(f"Could not parse appointment time: {e}")


# ── Entry Point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Focus CLI Reminder Engine")
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    args = parser.parse_args()

    memory.init_db()
    config = load_config()

    # Ensure reminder columns exist
    with memory.get_conn() as conn:
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN last_reminded_at TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN reminder_tier INTEGER DEFAULT 0")
        except Exception:
            pass

    if args.once:
        run_reminder_check(config)
        run_appointment_check(config)
        return

    interval = config["reminders"]["check_interval_minutes"] * 60
    log.info(f"Reminder daemon started — checking every {config['reminders']['check_interval_minutes']} minutes")

    while True:
        try:
            run_reminder_check(config)
            run_appointment_check(config)
        except Exception as e:
            log.error(f"Reminder check failed: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
