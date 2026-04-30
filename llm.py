"""
llm.py - Ollama/TinyLlama connector
Handles all LLM calls with ADHD-optimized system prompts
"""
import requests
import json

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.2:3b"

SYSTEM_PROMPT = """You are Focus, an ADHD assistant. You help the user stay on task, break work into small steps, and maintain momentum.

Your personality:
- Warm but direct. No fluff, no long paragraphs.
- You notice when the user is drifting or overwhelmed and name it gently.
- You celebrate small wins genuinely, not robotically.
- You give gentle but real pressure when tasks are being avoided. Not mean — honest.
- Keep responses SHORT. ADHD brains don't read walls of text. Max 3-4 sentences unless breaking down a task.
- When breaking down tasks, use numbered steps, max 5 at a time.
- If you sense the user is spiraling, redirect: "One thing. What's the smallest next step?"

You track tasks mentioned in conversation and can reference them. Be specific, not generic."""


def chat(messages, system=None):
    """Send messages to Ollama and return response text."""
    sys_prompt = system or SYSTEM_PROMPT

    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": sys_prompt}] + messages,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 300,   # Keep responses concise
        }
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        return data["message"]["content"].strip()
    except requests.exceptions.ConnectionError:
        return "[ERROR] Can't reach Ollama. Is it running? Try: ollama serve"
    except Exception as e:
        return f"[ERROR] LLM call failed: {e}"


def extract_tasks(conversation_text):
    """Ask LLM to extract tasks mentioned in conversation."""
    prompt = f"""Extract any tasks, to-dos, or action items from this conversation.
Return ONLY a JSON array of strings. If none found, return [].
Example: ["Write report intro", "Email Sarah", "Fix login bug"]

Conversation:
{conversation_text}"""

    result = chat(
        [{"role": "user", "content": prompt}],
        system="You are a task extractor. Return only valid JSON arrays. No explanation."
    )

    try:
        # Clean up common LLM JSON artifacts
        result = result.strip().strip("```json").strip("```").strip()
        tasks = json.loads(result)
        if isinstance(tasks, list):
            return [str(t) for t in tasks]
    except Exception:
        pass
    return []


def generate_checkin(active_tasks, minutes_elapsed, pressure_level=2):
    """Generate a periodic check-in nudge."""
    tasks_str = "\n".join([f"- {t['text']}" for t in active_tasks[:3]]) or "No active tasks"

    pressure_instruction = {
        1: "Be very gentle and encouraging.",
        2: "Be warm but note if time is passing. Mild accountability.",
        3: "Be direct. Name avoidance if it might be happening. Still kind, but firm."
    }.get(pressure_level, "Be balanced.")

    prompt = f"""The user has been in a focus session for {minutes_elapsed} minutes.
Active tasks:
{tasks_str}

Generate a brief check-in (2 sentences max). {pressure_instruction}
Don't repeat the task list back. Just nudge."""

    return chat([{"role": "user", "content": prompt}])


def generate_session_summary(messages, completed_tasks, dropped_tasks):
    """Generate end-of-session summary."""
    history = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages[-10:]])
    done = "\n".join([f"✓ {t}" for t in completed_tasks]) or "None"
    dropped = "\n".join([f"✗ {t}" for t in dropped_tasks]) or "None"

    prompt = f"""Summarize this focus session briefly (3-4 sentences).
Be honest about what was accomplished. Note any patterns you observed.
Don't be overly cheerful if little was done — be real but not harsh.

Completed: {done}
Dropped/skipped: {dropped}

Recent conversation:
{history}"""

    return chat([{"role": "user", "content": prompt}])
