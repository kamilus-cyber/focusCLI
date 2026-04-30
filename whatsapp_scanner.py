"""
whatsapp_scanner.py - WhatsApp message scanner for Focus CLI
Reads from whatsapp-mcp-extended's local SQLite database.
Extracts implied tasks from messages sent TO you by trusted contacts.

Security layers:
  1. Keyword filter — only messages matching task-like patterns reach the LLM
  2. Trust tiers — known contacts processed normally, unknown flagged for review
  3. Sandboxed extraction — LLM output validated before touching task DB

Usage:
  python3 whatsapp_scanner.py --once              # single scan (12h window)
  python3 whatsapp_scanner.py                     # daemon mode (hourly)
  python3 whatsapp_scanner.py --review            # show flagged unknown-sender messages
  python3 whatsapp_scanner.py --trust <number>    # mark a number as trusted
  python3 whatsapp_scanner.py --untrust <number>  # remove trust
  python3 whatsapp_scanner.py --list-trusted      # show all trusted contacts
  python3 whatsapp_scanner.py --dismiss-flagged   # clear all flagged messages
"""

import sys
import time
import json
import yaml
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import memory
import llm

CONFIG_PATH = Path(__file__).parent / "config.yaml"
WA_DB_PATH  = Path.home() / "whatsapp-mcp-extended/whatsapp-bridge/store/messages.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [wa_scanner] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("wa_scanner")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def init_trust_table():
    with memory.get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS wa_trusted_contacts (
                number TEXT PRIMARY KEY,
                name TEXT,
                trusted_since TEXT
            );
            CREATE TABLE IF NOT EXISTS wa_flagged_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT,
                message TEXT,
                timestamp TEXT,
                reviewed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS wa_processed_messages (
                message_id TEXT PRIMARY KEY,
                processed_at TEXT
            );
        """)


def is_trusted(number):
    clean = number.split("@")[0].strip()
    with memory.get_conn() as conn:
        row = conn.execute(
            "SELECT number FROM wa_trusted_contacts WHERE number=?", (clean,)
        ).fetchone()
    return row is not None


def add_trusted(number, name=""):
    clean = number.split("@")[0].strip()
    now = datetime.now().isoformat()
    with memory.get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO wa_trusted_contacts (number, name, trusted_since) VALUES (?,?,?)",
            (clean, name, now)
        )
    log.info(f"Trusted: +{clean} ({name or 'no name'})")


def remove_trusted(number):
    clean = number.split("@")[0].strip()
    with memory.get_conn() as conn:
        conn.execute("DELETE FROM wa_trusted_contacts WHERE number=?", (clean,))
    log.info(f"Untrusted: +{clean}")


def flag_message(number, message, timestamp):
    with memory.get_conn() as conn:
        conn.execute(
            "INSERT INTO wa_flagged_messages (number, message, timestamp) VALUES (?,?,?)",
            (number.split("@")[0], message, timestamp)
        )


def mark_processed(message_id):
    now = datetime.now().isoformat()
    with memory.get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO wa_processed_messages (message_id, processed_at) VALUES (?,?)",
            (str(message_id), now)
        )


def is_processed(message_id):
    with memory.get_conn() as conn:
        row = conn.execute(
            "SELECT message_id FROM wa_processed_messages WHERE message_id=?",
            (str(message_id),)
        ).fetchone()
    return row is not None


def show_flagged():
    with memory.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, number, message, timestamp FROM wa_flagged_messages "
            "WHERE reviewed=0 ORDER BY timestamp DESC LIMIT 20"
        ).fetchall()
    if not rows:
        print("No unreviewed flagged messages.")
        return
    print(f"\n⚠️  {len(rows)} flagged messages from unknown senders:\n")
    for row in rows:
        print(f"  [{row[0]}] +{row[1]} at {row[2][:16]}")
        print(f"       \"{row[3][:100]}\"")
        print()
    print("To trust a number:  python3 whatsapp_scanner.py --trust <number>")
    print("To dismiss all:     python3 whatsapp_scanner.py --dismiss-flagged\n")


def dismiss_flagged():
    with memory.get_conn() as conn:
        conn.execute("UPDATE wa_flagged_messages SET reviewed=1")
    log.info("All flagged messages dismissed.")


TASK_KEYWORDS = [
    # English
    "can you", "could you", "please", "don't forget", "remember",
    "call me", "call you", "meeting", "appointment", "deadline",
    "tomorrow", "today", "tonight", "this week", "by friday", "by monday",
    "need you to", "need to", "have to", "must", "urgent", "important",
    "pick up", "bring", "send me", "let me know", "confirm", "reply",
    "when you can", "as soon as", "before", "after work",
    # Dutch
    "kun je", "kan je", "kan jij", "vergeet niet", "onthoud", "bel me",
    "afspraak", "morgen", "vandaag", "vanavond", "deze week",
    "moet je", "moet ik", "breng", "stuur me", "laat me weten",
    "zo snel mogelijk", "voor", "na het werk", "ophalen", "zoeken",
    "papieren", "documenten", "regelen",
    # Polish
    "czy mozesz", "prosze", "nie zapomnij", "pamietaj", "zadzwon",
    "jutro", "dzisiaj", "dzis wieczor", "w tym tygodniu",
    "musisz", "musze", "przyniesc", "wyslij mi", "daj znac",
    "jak najszybciej", "przed", "po pracy", "odbierz",
    "spotkanie", "termin", "wazne", "pilne",
]


def passes_keyword_filter(text):
    if not text or len(text.strip()) < 5:
        return False
    return any(kw in text.lower() for kw in TASK_KEYWORDS)


def get_recent_messages(wa_conn, my_number, hours_back=12):
    since = (datetime.now() - timedelta(hours=hours_back)).strftime("%Y-%m-%d %H:%M:%S")
    rows = wa_conn.execute(
        """SELECT rowid, chat_jid, sender, timestamp, content
           FROM messages
           WHERE is_from_me = 0
           AND chat_jid NOT LIKE '%@g.us'
           AND content != ''
           AND content IS NOT NULL
           AND timestamp > ?
           ORDER BY timestamp DESC
           LIMIT 100""",
        (since,)
    ).fetchall()
    return [
        {
            "id": r[0],
            "chat_jid": r[1],
            "sender": r[2].split("@")[0],
            "timestamp": r[3],
            "content": r[4]
        }
        for r in rows
    ]


def get_contact_name(number):
    try:
        wa_db = WA_DB_PATH.parent / "whatsapp.db"
        if not wa_db.exists():
            return None
        conn2 = sqlite3.connect(str(wa_db))
        row = conn2.execute(
            "SELECT name FROM contacts WHERE jid LIKE ? LIMIT 1",
            (f"{number}%",)
        ).fetchone()
        conn2.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def extract_task_from_message(sender, content, contact_name=None):
    name = contact_name or f"+{sender}"

    prompt = f"""{name} sent you this WhatsApp message:
"{content}"

Extract the task or request in ONE sentence starting with a verb.
Be specific. Include who asked and what exactly is needed.

Examples of good responses:
- "Let {name} know if they can come at 18:30 to pick up the ESP32"
- "Find court papers from the rechtbank for {name}"
- "Call {name} back — they are waiting"
- "Reply to {name} about the meeting time"

If there is genuinely no task or request, reply: NO_TASK
Reply with ONLY the task sentence or NO_TASK. Nothing else."""

    result = llm.chat(
        [{"role": "user", "content": prompt}],
        system="You are a task extractor. Reply with exactly one task sentence starting with a verb, or NO_TASK. No explanation, no preamble, no quotes."
    )

    result = result.strip().strip('"').strip("'")

    if not result or result == "NO_TASK" or len(result) < 8:
        return None
    if len(result) > 200:
        log.warning(f"Extraction too long ({len(result)} chars) — discarded")
        return None

    injection_patterns = [
        "ignore previous", "system:", "assistant:", "forget your",
        "new instructions", "disregard", "override"
    ]
    if any(p in result.lower() for p in injection_patterns):
        log.warning("Possible injection attempt — discarded")
        return None

    vague_patterns = [
        "respond with", "provide a", "give a", "create a task",
        "specific task", "action item", "here is", "here's a"
    ]
    if any(p in result.lower() for p in vague_patterns):
        log.warning(f"Vague extraction rejected: {result}")
        return None

    return result


def is_duplicate_task(task_text, existing_tasks):
    task_words = set(task_text.lower().split())
    for existing in existing_tasks:
        existing_words = set(existing["text"].lower().split())
        if not task_words:
            continue
        overlap = len(task_words & existing_words) / len(task_words)
        if overlap > 0.6:
            return True
    return False


def run_whatsapp_scan(config, hours_back=12):
    if not WA_DB_PATH.exists():
        log.error(f"WhatsApp DB not found at {WA_DB_PATH}")
        log.error("Is whatsapp-mcp-extended bridge running?")
        return

    wa_conn = sqlite3.connect(str(WA_DB_PATH))

    my_number = config.get("whatsapp", {}).get("my_number", "")
    if not my_number:
        log.error("Set your number in config.yaml: whatsapp.my_number")
        wa_conn.close()
        return

    log.info(f"Scanning WhatsApp (last {hours_back}h)...")
    messages = get_recent_messages(wa_conn, my_number, hours_back)
    log.info(f"Found {len(messages)} incoming messages")

    existing_tasks = memory.get_active_tasks()
    new_tasks = 0
    filtered = 0
    flagged = 0
    already_done = 0

    for msg in messages:
        msg_id    = msg["id"]
        sender    = msg["sender"]
        content   = msg["content"]
        timestamp = msg["timestamp"]

        if is_processed(msg_id):
            already_done += 1
            continue

        if not passes_keyword_filter(content):
            filtered += 1
            mark_processed(msg_id)
            continue

        if not is_trusted(sender):
            log.info(f"  ⚠️  Unknown +{sender} — flagging for review")
            flag_message(sender, content, timestamp)
            flagged += 1
            mark_processed(msg_id)
            continue

        contact_name = get_contact_name(sender)
        log.info(f"  Processing +{sender} ({contact_name or 'no name'}): {content[:60]}...")

        task = extract_task_from_message(sender, content, contact_name)

        if task:
            if not is_duplicate_task(task, existing_tasks):
                memory.add_task(f"{task} [WhatsApp]", priority=2)
                existing_tasks = memory.get_active_tasks()
                new_tasks += 1
                log.info(f"  ✓ Added: {task}")
            else:
                log.info(f"  ~ Duplicate skipped: {task}")
        else:
            log.info(f"  - No task found")

        mark_processed(msg_id)

    wa_conn.close()
    log.info(
        f"Done — {new_tasks} added, {filtered} filtered, "
        f"{flagged} flagged, {already_done} already processed"
    )
    if flagged > 0:
        log.info("  Run --review to see flagged messages")


def main():
    parser = argparse.ArgumentParser(description="Focus WhatsApp Scanner")
    parser.add_argument("--once",            action="store_true", help="Scan once and exit")
    parser.add_argument("--review",          action="store_true", help="Show flagged messages")
    parser.add_argument("--trust",           metavar="NUMBER",    help="Trust a phone number")
    parser.add_argument("--untrust",         metavar="NUMBER",    help="Remove trust")
    parser.add_argument("--list-trusted",    action="store_true", help="List trusted contacts")
    parser.add_argument("--dismiss-flagged", action="store_true", help="Dismiss all flagged")
    args = parser.parse_args()

    memory.init_db()
    init_trust_table()
    config = load_config()

    if args.review:         show_flagged();  return
    if args.dismiss_flagged: dismiss_flagged(); return

    if args.trust:
        add_trusted(args.trust)
        print(f"✓ +{args.trust.split('@')[0]} is now trusted")
        return

    if args.untrust:
        remove_trusted(args.untrust)
        print(f"✓ +{args.untrust.split('@')[0]} removed from trusted")
        return

    if args.list_trusted:
        with memory.get_conn() as conn:
            rows = conn.execute(
                "SELECT number, name, trusted_since FROM wa_trusted_contacts ORDER BY trusted_since"
            ).fetchall()
        if rows:
            print("\nTrusted contacts:")
            for r in rows:
                print(f"  +{r[0]}  {r[1] or '(no name)'}  since {r[2][:10]}")
        else:
            print("No trusted contacts. Add with: --trust <number>")
        return

    if args.once:
        run_whatsapp_scan(config, hours_back=12)
        return

    interval = config["scanner"]["interval_minutes"] * 60
    log.info(f"WhatsApp scanner daemon started — every {config['scanner']['interval_minutes']} min")
    while True:
        try:
            run_whatsapp_scan(config, hours_back=config["scanner"]["interval_minutes"] / 60 + 0.5)
        except Exception as e:
            log.error(f"Scan failed: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
