#!/usr/bin/env python3
"""
main.py - Focus CLI entry point
ADHD-optimized assistant wrapper for local LLM via Ollama
"""
import sys
import uuid
from datetime import datetime
from rich.console import Console
from rich.spinner import Spinner
from rich.live import Live

import memory
import llm
import focus as focus_ui

console = Console()


def handle_command(cmd_raw, session, session_id):
    parts = cmd_raw.strip().split(maxsplit=2)
    cmd = parts[0].lower()

    if cmd in ("/quit", "/q", "/exit"):
        end_session(session, session_id)
        sys.exit(0)

    elif cmd == "/tasks":
        tasks = memory.get_active_tasks()
        if tasks:
            console.print("\n[bold]📋 Active tasks:[/bold]")
            for t in tasks:
                icon = "🔴" if t["priority"] == 1 else "🟡" if t["priority"] == 2 else "⚪"
                console.print(f"  {icon} [{t['id']}] {t['text']}")
        else:
            console.print("[dim]No active tasks.[/dim]")
        console.print()
        return True

    elif cmd == "/done" and len(parts) > 1:
        try:
            task_id = int(parts[1])
            memory.complete_task(task_id)
            console.print(f"[green]✓ Task {task_id} done. Nice work.[/green]\n")
        except ValueError:
            console.print("[red]Usage: /done <task_id>[/red]\n")
        return True

    elif cmd == "/drop" and len(parts) > 1:
        try:
            task_id = int(parts[1])
            memory.drop_task(task_id)
            console.print(f"[dim]Task {task_id} dropped. Moving on.[/dim]\n")
        except ValueError:
            console.print("[red]Usage: /drop <task_id>[/red]\n")
        return True

    elif cmd == "/add" and len(parts) > 1:
        task_text = " ".join(parts[1:])
        memory.add_task(task_text, session_id=session_id)
        console.print(f"[green]📌 Added: {task_text}[/green]\n")
        return True

    elif cmd == "/priority" and len(parts) > 2:
        try:
            task_id = int(parts[1])
            priority = int(parts[2])
            if priority not in (1, 2, 3):
                raise ValueError
            with memory.get_conn() as conn:
                conn.execute("UPDATE tasks SET priority=? WHERE id=?", (priority, task_id))
            console.print(f"[dim]Priority updated.[/dim]\n")
        except ValueError:
            console.print("[red]Usage: /priority <id> <1|2|3>[/red]\n")
        return True

    elif cmd == "/pressure" and len(parts) > 1:
        try:
            level = int(parts[1])
            if level not in (1, 2, 3):
                raise ValueError
            session.pressure_level = level
            labels = {1: "gentle 🌸", 2: "balanced ⚖️", 3: "firm 🎯"}
            console.print(f"[dim]Pressure set to {labels[level]}[/dim]\n")
        except ValueError:
            console.print("[red]Usage: /pressure <1|2|3>[/red]\n")
        return True

    elif cmd == "/clear":
        tasks = memory.get_active_tasks()
        last = memory.get_last_session_summary()
        focus_ui.print_header(tasks, last)
        return True

    elif cmd == "/help":
        focus_ui.print_help()
        return True

    return False


def end_session(session, session_id):
    elapsed = session.elapsed_minutes()

    with memory.get_conn() as conn:
        done = conn.execute(
            "SELECT text FROM tasks WHERE session_id=? AND status='done'",
            (session_id,)
        ).fetchall()

    completed = [r[0] for r in done]

    if completed:
        console.print(f"\n[bold cyan]Session done ({elapsed} min)[/bold cyan]")
        console.print("[green]Completed:[/green]")
        for t in completed:
            console.print(f"  ✓ {t}")
    else:
        console.print(f"\n[bold cyan]Session ended ({elapsed} min)[/bold cyan]")
        console.print("[dim]No tasks completed. That's okay — tomorrow is fresh.[/dim]")

    summary = f"{elapsed} min session. Completed: {len(completed)} tasks."
    memory.end_session(session_id, summary=summary)
    console.print("\n[dim]See you next time. ⚡[/dim]\n")
    session.stop()


def main():
    memory.init_db()

    session_id = str(uuid.uuid4())[:8]
    session = focus_ui.FocusSession(session_id, pressure_level=2)

    tasks = memory.get_active_tasks()
    last = memory.get_last_session_summary()
    focus_ui.print_header(tasks, last)
    memory.start_session(session_id)

    # Hardcoded opening — no LLM, no hallucinations
    if tasks:
        first = tasks[0]["text"].replace("[APPOINTMENT] ", "")
        if len(first) > 60:
            first = first[:60] + "..."
        opening = f"Hey. First up: {first} — want to start there?"
    else:
        opening = "Hey! No tasks yet — what are we working on today?"

    console.print(f"[bold cyan]Focus:[/bold cyan] {opening}\n")
    memory.save_message(session_id, "assistant", opening)

    session.start_checkin_timer(lambda: None)

    conversation = [{"role": "assistant", "content": opening}]

    while True:
        if session.checkin_pending:
            session.checkin_pending = False
            active = memory.get_active_tasks()
            checkin_msg = llm.generate_checkin(
                active, session.elapsed_minutes(), session.pressure_level
            )
            focus_ui.print_checkin(checkin_msg)

        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            end_session(session, session_id)
            sys.exit(0)

        if not user_input:
            continue

        if user_input.startswith("/"):
            handle_command(user_input, session, session_id)
            continue

        memory.save_message(session_id, "user", user_input)
        conversation.append({"role": "user", "content": user_input})

        with Live(Spinner("dots", text="..."), refresh_per_second=10, transient=True):
            response = llm.chat(conversation[-20:])

        console.print(f"\n[bold cyan]Focus:[/bold cyan] {response}\n")
        memory.save_message(session_id, "assistant", response)
        conversation.append({"role": "assistant", "content": response})

        # Auto-extract tasks — only if result is meaningful
        recent_text = f"User: {user_input}\nAssistant: {response}"
        extracted = llm.extract_tasks(recent_text)
        if extracted:
            for task_text in extracted:
                existing = [t["text"].lower() for t in memory.get_active_tasks()]
                if task_text.lower() not in existing and len(task_text) > 8:
                    memory.add_task(task_text, session_id=session_id)
            focus_ui.print_task_extracted(extracted)


if __name__ == "__main__":
    main()
