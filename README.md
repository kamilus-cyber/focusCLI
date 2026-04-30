# ⚡ Focus CLI

An ADHD-focused task manager that runs entirely on your machine. No cloud, no subscription, no bloat.

Built around one insight: ADHD brains don't need more features. They need less friction and honest pressure.

---

## What it does

- Conversational task tracking via local LLM (Ollama)
- Auto-extracts tasks from conversation — just talk, it listens
- WhatsApp scanner — reads incoming messages from trusted contacts and extracts implied tasks
- Escalating reminders via SMS or WhatsApp (Twilio) — gentle at first, increasingly honest
- 15-minute check-ins with adjustable pressure levels
- Mobile web interface over Tailscale — manage tasks from your phone
- Everything stored locally in SQLite — your data stays on your machine

---

## Requirements

- Python 3.9+
- [Ollama](https://ollama.com) running locally
- `ollama pull llama3.2:3b`
- Twilio account (optional — for SMS/WhatsApp reminders)

---

## Installation

```bash
git clone https://github.com/yourname/focus-cli
cd focus-cli
pip install -r requirements.txt --break-system-packages
cp config.example.yaml config.yaml
python main.py
```

---

## Usage

Just type. Focus listens and extracts tasks automatically from conversation.

### Commands

| Command | What it does |
|---------|-------------|
| `/tasks` | Show all active tasks |
| `/done <id>` | Mark task complete |
| `/drop <id>` | Drop a task (not failure — reprioritizing) |
| `/add <text>` | Manually add a task |
| `/priority <id> <1\|2\|3>` | Set priority (1=high) |
| `/pressure <1\|2\|3>` | Adjust check-in pressure |
| `/clear` | Clear screen, show task list |
| `/help` | Show help |
| `/quit` | End session with summary |

### Pressure levels

| Level | Behavior |
|-------|----------|
| 1 | Gentle encouragement only |
| 2 | Balanced nudges + accountability (default) |
| 3 | Direct. Calls out avoidance. Still kind. |

---

## WhatsApp integration

The scanner reads your local WhatsApp database (via [whatsapp-mcp-extended](https://github.com/lharries/whatsapp-mcp)) and extracts implied tasks from messages sent by trusted contacts.

Security layers:
- Keyword filter — only task-like messages reach the LLM
- Trust tiers — unknown senders are flagged for review, never auto-processed
- Injection detection — LLM output validated before touching the task database

```bash
python whatsapp_scanner.py --trust <number>   # add trusted contact
python whatsapp_scanner.py --once             # scan once
python whatsapp_scanner.py                    # run as daemon
python whatsapp_scanner.py --review           # review flagged messages
```

---

## Web interface

Access your tasks from any device on your Tailscale network:

```bash
python web.py
# Open http://<your-machine>:5000
```

---

## Architecture

```
focus_cli/
├── main.py              # REPL loop + command handling
├── memory.py            # SQLite: tasks, sessions, messages
├── llm.py               # Ollama connector + ADHD-tuned prompts
├── focus.py             # Session timer, check-in engine, UI
├── reminder.py          # Escalating reminder daemon (Twilio)
├── whatsapp_scanner.py  # WhatsApp task extractor
├── web.py               # Mobile-friendly web interface
└── config.yaml          # Your settings
```

Data stored in `~/.focus_cli/memory.db` — persists between sessions.

---

## Design philosophy

Modeled on the AK-47 and the Mercedes W123. Maximum reliability, minimum complexity. The LLM has one job: help you stay on task. It does not explain itself at length or pad responses. Three sentences maximum.

---

## License

MIT — see LICENSE
