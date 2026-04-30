"""
focus.py - Session management and pressure engine
Handles timing, check-ins, and task momentum tracking
"""
import time
import threading
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

CHECKIN_INTERVAL = 15 * 60   # Check in every 15 minutes
PRESSURE_LEVEL = 2            # Global default (1=soft, 2=balanced, 3=firm)


class FocusSession:
    def __init__(self, session_id, pressure_level=2):
        self.session_id = session_id
        self.pressure_level = pressure_level
        self.started_at = datetime.now()
        self.checkin_pending = False
        self._timer = None
        self._running = False

    def start_checkin_timer(self, callback):
        """Start background timer that sets a check-in flag."""
        self._running = True
        self._callback = callback
        self._schedule_next()

    def _schedule_next(self):
        if not self._running:
            return
        self._timer = threading.Timer(CHECKIN_INTERVAL, self._trigger)
        self._timer.daemon = True
        self._timer.start()

    def _trigger(self):
        self.checkin_pending = True
        if self._callback:
            self._callback()
        self._schedule_next()

    def stop(self):
        self._running = False
        if self._timer:
            self._timer.cancel()

    def elapsed_minutes(self):
        delta = datetime.now() - self.started_at
        return int(delta.total_seconds() / 60)


def print_header(active_tasks, last_session=None):
    """Print the startup screen."""
    console.clear()

    # Title
    console.print(Panel(
        "[bold cyan]⚡ FOCUS[/bold cyan] [dim]— ADHD Assistant[/dim]",
        border_style="cyan",
        expand=False
    ))

    # Last session recap
    if last_session and last_session.get("summary"):
        console.print(f"\n[dim]Last session:[/dim] {last_session['summary']}\n")

    # Active tasks
    if active_tasks:
        console.print("[bold]📋 Active tasks:[/bold]")
        for t in active_tasks[:5]:  # Max 5, don't overwhelm
            priority_icon = "🔴" if t["priority"] == 1 else "🟡" if t["priority"] == 2 else "⚪"
            console.print(f"  {priority_icon} [{t['id']}] {t['text']}")
    else:
        console.print("[dim]No active tasks. Tell me what you're working on.[/dim]")

    console.print("\n[dim]Commands: /done <id> · /drop <id> · /tasks · /quit · /help[/dim]")
    console.print("[dim]─────────────────────────────────────────────────────[/dim]\n")


def print_checkin(message):
    """Print a non-intrusive check-in between responses."""
    console.print(f"\n[bold yellow]⏰ CHECK-IN:[/bold yellow] {message}\n")


def print_task_extracted(tasks):
    """Notify user that tasks were auto-extracted."""
    if tasks:
        console.print(f"\n[dim green]📌 Saved to tasks: {', '.join(tasks)}[/dim green]\n")


def print_help():
    console.print(Panel("""
[bold]Chat commands:[/bold]
  Just type — I'll help you focus and track tasks naturally.

[bold]Task commands:[/bold]
  /tasks          — show all active tasks
  /done <id>      — mark task as complete  
  /drop <id>      — drop a task (not failing, just reprioritizing)
  /add <text>     — manually add a task
  /priority <id> <1|2|3>  — set priority (1=high)

[bold]Session:[/bold]
  /quit or /q     — end session with summary
  /pressure <1|2|3>  — adjust pressure level
  /clear          — clear screen and show tasks

[bold]Pressure levels:[/bold]
  1 = gentle encouragement only
  2 = balanced nudges + accountability  
  3 = direct, calls out avoidance
""", title="Help", border_style="dim"))
