#!/bin/bash
# start.sh - Launch all Focus CLI daemons
# Usage: bash start.sh
#        bash start.sh --stop
#        bash start.sh --status

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="$HOME/.focus_cli/pids"
mkdir -p "$PID_DIR"

stop_all() {
    echo "Stopping Focus CLI daemons..."
    for pid_file in "$PID_DIR"/*.pid; do
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            name=$(basename "$pid_file" .pid)
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid"
                echo "  Stopped $name (pid $pid)"
            fi
            rm "$pid_file"
        fi
    done
    echo "All daemons stopped."
    exit 0
}

status_all() {
    echo "Focus CLI daemon status:"
    for name in scanner whatsapp reminder; do
        pid_file="$PID_DIR/$name.pid"
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            if kill -0 "$pid" 2>/dev/null; then
                echo "  ✓ $name running (pid $pid)"
            else
                echo "  ✗ $name dead (stale pid)"
                rm "$pid_file"
            fi
        else
            echo "  - $name not running"
        fi
    done
}

if [ "$1" == "--stop" ];   then stop_all; fi
if [ "$1" == "--status" ]; then status_all; exit 0; fi

echo "⚡ Starting Focus CLI daemons..."

# Check Ollama
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "⚠️  Ollama doesn't seem to be running. Start it with: ollama serve"
fi

# Check WhatsApp bridge
WA_DB="$HOME/whatsapp-mcp-extended/whatsapp-bridge/store/messages.db"
if [ ! -f "$WA_DB" ]; then
    echo "⚠️  WhatsApp bridge DB not found. Start the bridge first:"
    echo "    cd ~/whatsapp-mcp-extended/whatsapp-bridge && DISABLE_AUTH_CHECK=true go run main.go"
fi

# Start Google/email/calendar scanner
python3 "$SCRIPT_DIR/scanner.py" >> "$HOME/.focus_cli/scanner.log" 2>&1 &
echo $! > "$PID_DIR/scanner.pid"
echo "  ✓ Scanner started (pid $!)"

# Start WhatsApp scanner
python3 "$SCRIPT_DIR/whatsapp_scanner.py" >> "$HOME/.focus_cli/whatsapp.log" 2>&1 &
echo $! > "$PID_DIR/whatsapp.pid"
echo "  ✓ WhatsApp scanner started (pid $!)"

# Start reminder engine
python3 "$SCRIPT_DIR/reminder.py" >> "$HOME/.focus_cli/reminder.log" 2>&1 &
echo $! > "$PID_DIR/reminder.pid"
echo "  ✓ Reminder engine started (pid $!)"

echo ""
echo "All daemons running. Logs:"
echo "  tail -f ~/.focus_cli/scanner.log"
echo "  tail -f ~/.focus_cli/whatsapp.log"
echo "  tail -f ~/.focus_cli/reminder.log"
echo ""
echo "To stop:   bash start.sh --stop"
echo "To check:  bash start.sh --status"
echo ""
echo "Now start your session:"
echo "  python3 $SCRIPT_DIR/main.py"
