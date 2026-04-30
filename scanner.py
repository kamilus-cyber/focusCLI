"""
scanner.py - Gmail + Google Calendar scanner for Focus CLI
Runs hourly, extracts tasks and appointments into the SQLite database.

Usage:
  python3 scanner.py --auth     # First-time Google OAuth (opens browser)
  python3 scanner.py --once     # Single scan, then exit
  python3 scanner.py            # Run as daemon (hourly loop)
"""
import os
import sys
import time
import yaml
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import base64
import re

import memory
import llm

# ── Config ─────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "config.yaml"

def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scanner] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("scanner")

# ── Google Auth ────────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly"
]


def get_google_credentials(config):
    creds_file = Path(__file__).parent / config["google"]["credentials_file"]
    token_file = Path(__file__).parent / config["google"]["token_file"]
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing Google token...")
            creds.refresh(Request())
        else:
            log.info("Starting Google OAuth flow — browser will open...")
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_file, "w") as f:
            f.write(creds.to_json())
        log.info(f"Token saved to {token_file}")

    return creds


# ── Gmail ──────────────────────────────────────────────────────────────────

def decode_email_body(payload):
    """Extract plain text from email payload."""
    body = ""
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain":
                data = part["body"].get("data", "")
                body += base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            elif "parts" in part:
                body += decode_email_body(part)
    elif payload.get("mimeType") == "text/plain":
        data = payload["body"].get("data", "")
        body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
    return body[:2000]  # Cap at 2000 chars to keep TinyLlama happy


def get_email_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def scan_gmail(service, config, since_hours=2):
    """Fetch recent emails from configured labels."""
    results = []
    labels = config["scanner"]["gmail_labels"]
    max_emails = config["scanner"].get("max_emails_per_scan", 20)

    # Only look at emails from the last scan window
    after_timestamp = int(time.time()) - (since_hours * 3600)

    for label in labels:
        try:
            response = service.users().messages().list(
                userId="me",
                labelIds=[label],
                q=f"after:{after_timestamp}",
                maxResults=max_emails
            ).execute()

            messages = response.get("messages", [])
            log.info(f"Gmail [{label}]: found {len(messages)} recent messages")

            for msg_ref in messages:
                msg = service.users().messages().get(
                    userId="me",
                    id=msg_ref["id"],
                    format="full"
                ).execute()

                headers = msg["payload"].get("headers", [])
                subject = get_email_header(headers, "subject")
                sender = get_email_header(headers, "from")
                body = decode_email_body(msg["payload"])
                date = get_email_header(headers, "date")

                results.append({
                    "source": f"gmail:{label}",
                    "subject": subject,
                    "sender": sender,
                    "body": body,
                    "date": date,
                    "id": msg_ref["id"]
                })

        except Exception as e:
            log.warning(f"Gmail scan failed for label {label}: {e}")

    return results


# ── Google Calendar ────────────────────────────────────────────────────────

def scan_calendar(service, config):
    """Fetch upcoming calendar events."""
    results = []
    days_ahead = config["scanner"].get("calendar_days_ahead", 7)

    now = datetime.now(timezone.utc).isoformat()
    from datetime import timedelta
    future = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).isoformat()
    
    try:
        events_result = service.events().list(
            calendarId="primary",
            timeMin=now,
            timeMax=future,
            maxResults=20,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        log.info(f"Calendar: found {len(events)} upcoming events")

        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date", ""))
            results.append({
                "source": "calendar",
                "title": event.get("summary", "Untitled event"),
                "start": start,
                "description": event.get("description", ""),
                "location": event.get("location", ""),
                "id": event["id"]
            })

    except Exception as e:
        log.warning(f"Calendar scan failed: {e}")

    return results


# ── Task Extraction ────────────────────────────────────────────────────────

def extract_tasks_from_email(email):
    """Use TinyLlama to find tasks/action items in an email."""
    text = f"Subject: {email['subject']}\nFrom: {email['sender']}\n\n{email['body']}"

    prompt = f"""Read this email and extract any action items, tasks, or things the recipient needs to do.
Return ONLY a JSON array of strings. Be specific. If none, return [].
Include deadlines if mentioned.

Email:
{text[:1500]}"""

    result = llm.chat(
        [{"role": "user", "content": prompt}],
        system="You are a task extractor. Return only valid JSON arrays. No explanation, no markdown."
    )

    try:
        result = result.strip().strip("```json").strip("```").strip()
        tasks = json.loads(result)
        if isinstance(tasks, list):
            return [str(t) for t in tasks if t]
    except Exception:
        pass
    return []


def format_calendar_task(event):
    """Convert calendar event to a task string."""
    start = event["start"]
    title = event["title"]

    # Parse and format the date nicely
    try:
        if "T" in start:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            formatted = dt.strftime("%a %b %d at %H:%M")
        else:
            formatted = start
    except Exception:
        formatted = start

    task = f"[APPOINTMENT] {title} — {formatted}"
    if event.get("location"):
        task += f" @ {event['location']}"
    return task


# ── Deduplication ──────────────────────────────────────────────────────────

def is_duplicate_task(task_text, existing_tasks):
    """Simple fuzzy dedup — avoid adding nearly identical tasks."""
    task_lower = task_text.lower().strip()
    for existing in existing_tasks:
        existing_lower = existing["text"].lower().strip()
        # Check if 70%+ of words overlap
        words_new = set(task_lower.split())
        words_old = set(existing_lower.split())
        if len(words_new) == 0:
            continue
        overlap = len(words_new & words_old) / len(words_new)
        if overlap > 0.7:
            return True
    return False


# ── Main Scan ──────────────────────────────────────────────────────────────

def run_scan(config, creds):
    """Run a full scan cycle."""
    log.info("Starting scan...")
    scan_start = datetime.now()

    gmail_service = build("gmail", "v1", credentials=creds)
    calendar_service = build("calendar", "v3", credentials=creds)

    existing_tasks = memory.get_active_tasks()
    new_tasks_added = 0

    # ── Calendar events ──
    events = scan_calendar(calendar_service, config)
    for event in events:
        task_text = format_calendar_task(event)
        if not is_duplicate_task(task_text, existing_tasks):
            memory.add_task(task_text, priority=1)  # Calendar = high priority
            existing_tasks = memory.get_active_tasks()  # Refresh for dedup
            new_tasks_added += 1
            log.info(f"  + Calendar: {task_text}")

    # ── Gmail ──
    emails = scan_gmail(gmail_service, config, since_hours=config["scanner"]["interval_minutes"] / 60 + 0.5)
    for email in emails:
        tasks = extract_tasks_from_email(email)
        for task_text in tasks:
            if not is_duplicate_task(task_text, existing_tasks):
                source_note = f" [from: {email['sender'][:30]}]"
                full_task = task_text + source_note
                memory.add_task(full_task, priority=2)
                existing_tasks = memory.get_active_tasks()
                new_tasks_added += 1
                log.info(f"  + Email task: {task_text}")

    elapsed = (datetime.now() - scan_start).seconds
    log.info(f"Scan complete in {elapsed}s — {new_tasks_added} new tasks added")
    return new_tasks_added


# ── Entry Point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Focus CLI Scanner")
    parser.add_argument("--auth", action="store_true", help="Run Google OAuth and exit")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    args = parser.parse_args()

    memory.init_db()
    config = load_config()

    log.info("Authenticating with Google...")
    creds = get_google_credentials(config)

    if args.auth:
        log.info("Auth successful. You can now run scanner.py normally.")
        return

    if args.once:
        run_scan(config, creds)
        return

    # Daemon mode
    interval = config["scanner"]["interval_minutes"] * 60
    log.info(f"Scanner daemon started — checking every {config['scanner']['interval_minutes']} minutes")

    while True:
        try:
            run_scan(config, creds)
        except Exception as e:
            log.error(f"Scan failed: {e}")
        log.info(f"Next scan in {config['scanner']['interval_minutes']} minutes...")
        time.sleep(interval)


if __name__ == "__main__":
    main()
