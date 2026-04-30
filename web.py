"""
web.py - Mobile-friendly task manager for Focus CLI
Accessible over Tailscale. No extra dependencies.

Usage:
  python3 web.py           # runs on 0.0.0.0:5000
  python3 web.py --port 8080
"""
import sys
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, parse_qsl

import memory

PRIORITY_LABEL = {1: "high", 2: "normal", 3: "low"}
PRIORITY_COLOR  = {1: "#e74c3c", 2: "#3498db", 3: "#95a5a6"}

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Focus</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, sans-serif; background: #111; color: #eee; padding: 16px; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 16px; color: #fff; }}

  /* Add task form */
  .add-form {{ display: flex; gap: 8px; margin-bottom: 24px; }}
  .add-form input[name=text] {{ flex: 1; padding: 12px; border-radius: 8px; border: none;
    background: #222; color: #eee; font-size: 1rem; }}
  .add-form select {{ padding: 12px 8px; border-radius: 8px; border: none;
    background: #222; color: #eee; font-size: 0.9rem; }}
  .add-form button {{ padding: 12px 18px; border-radius: 8px; border: none;
    background: #27ae60; color: #fff; font-size: 1rem; font-weight: bold; cursor: pointer; }}

  /* Task list */
  .task {{ background: #1a1a1a; border-radius: 10px; padding: 12px 14px;
    margin-bottom: 10px; display: flex; align-items: flex-start; gap: 10px; }}
  .task-body {{ flex: 1; min-width: 0; }}
  .task-text {{ font-size: 0.95rem; word-break: break-word; margin-bottom: 6px; }}
  .badge {{ display: inline-block; font-size: 0.7rem; padding: 2px 7px;
    border-radius: 4px; font-weight: bold; color: #fff; margin-right: 4px; }}
  .task-actions {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }}
  .task-actions button, .task-actions a {{
    padding: 6px 12px; border-radius: 6px; border: none; font-size: 0.8rem;
    cursor: pointer; color: #fff; text-decoration: none; display: inline-block; }}
  .btn-done   {{ background: #27ae60; }}
  .btn-drop   {{ background: #7f8c8d; }}
  .btn-edit   {{ background: #2980b9; }}
  .btn-high   {{ background: #e74c3c; }}
  .btn-normal {{ background: #3498db; }}
  .btn-low    {{ background: #95a5a6; }}

  /* Inline edit */
  .edit-form {{ display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }}
  .edit-form input {{ flex: 1; min-width: 0; padding: 8px; border-radius: 6px;
    border: none; background: #333; color: #eee; font-size: 0.9rem; }}
  .edit-form button {{ padding: 8px 14px; border-radius: 6px; border: none;
    background: #2980b9; color: #fff; font-size: 0.85rem; cursor: pointer; }}

  .section-title {{ font-size: 0.75rem; text-transform: uppercase; color: #777;
    margin: 20px 0 8px; letter-spacing: 0.08em; }}
  .empty {{ color: #555; font-size: 0.9rem; padding: 8px 0; }}
</style>
</head>
<body>
<h1>Focus</h1>

<form class="add-form" method="POST" action="/add">
  <input name="text" type="text" placeholder="New task..." autocomplete="off" autofocus required>
  <select name="priority">
    <option value="1">High</option>
    <option value="2" selected>Normal</option>
    <option value="3">Low</option>
  </select>
  <button type="submit">+</button>
</form>

{task_sections}

</body>
</html>
"""

TASK_HTML = """\
<div class="task">
  <div class="task-body">
    <div class="task-text">{text}</div>
    <span class="badge" style="background:{color}">{priority}</span>
    {edit_section}
    <div class="task-actions">
      <form method="POST" action="/done/{id}" style="display:inline">
        <button class="btn-done" type="submit">Done</button>
      </form>
      <form method="POST" action="/drop/{id}" style="display:inline">
        <button class="btn-drop" type="submit">Drop</button>
      </form>
      <a class="btn-edit" href="/?edit={id}">Edit</a>
      {priority_buttons}
    </div>
  </div>
</div>
"""

EDIT_SECTION = """\
<form class="edit-form" method="POST" action="/edit/{id}">
  <input name="text" value="{text}" autocomplete="off">
  <button type="submit">Save</button>
</form>
"""

PRIORITY_BUTTONS = {
    1: '<form method="POST" action="/priority/{id}/2" style="display:inline"><button class="btn-normal" type="submit">Normal</button></form>'
       '<form method="POST" action="/priority/{id}/3" style="display:inline"><button class="btn-low" type="submit">Low</button></form>',
    2: '<form method="POST" action="/priority/{id}/1" style="display:inline"><button class="btn-high" type="submit">High</button></form>'
       '<form method="POST" action="/priority/{id}/3" style="display:inline"><button class="btn-low" type="submit">Low</button></form>',
    3: '<form method="POST" action="/priority/{id}/1" style="display:inline"><button class="btn-high" type="submit">High</button></form>'
       '<form method="POST" action="/priority/{id}/2" style="display:inline"><button class="btn-normal" type="submit">Normal</button></form>',
}


def render_tasks(edit_id=None):
    tasks = memory.get_active_tasks()
    if not tasks:
        return '<p class="empty">No active tasks. Add one above.</p>'

    groups = {1: [], 2: [], 3: []}
    for t in tasks:
        groups[t["priority"]].append(t)

    sections = []
    labels = {1: "High priority", 2: "Normal", 3: "Low priority"}
    for pri in (1, 2, 3):
        group = groups[pri]
        if not group:
            continue
        html = f'<div class="section-title">{labels[pri]}</div>'
        for t in group:
            editing = (str(t["id"]) == str(edit_id))
            edit_sec = EDIT_SECTION.format(id=t["id"], text=t["text"].replace('"', "&quot;")) if editing else ""
            pri_btns = PRIORITY_BUTTONS[t["priority"]].replace("{id}", str(t["id"]))
            safe_text = t["text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html += TASK_HTML.format(
                id=t["id"],
                text=safe_text,
                color=PRIORITY_COLOR[t["priority"]],
                priority=PRIORITY_LABEL[t["priority"]],
                edit_section=edit_sec,
                priority_buttons=pri_btns,
            )
        sections.append(html)
    return "\n".join(sections)


class FocusHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence default logging

    def send_redirect(self, location="/"):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def send_html(self, body):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def read_form(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode()
        return dict(parse_qsl(raw))

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        edit_id = qs.get("edit", [None])[0]
        body = HTML_TEMPLATE.format(task_sections=render_tasks(edit_id))
        self.send_html(body)

    def do_POST(self):
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        form = self.read_form()

        if parts == ["add"]:
            text = form.get("text", "").strip()
            priority = int(form.get("priority", 2))
            if text:
                memory.add_task(text, priority=priority)

        elif len(parts) == 2 and parts[0] == "done":
            memory.complete_task(int(parts[1]))

        elif len(parts) == 2 and parts[0] == "drop":
            memory.drop_task(int(parts[1]))

        elif len(parts) == 2 and parts[0] == "edit":
            text = form.get("text", "").strip()
            if text:
                task_id = int(parts[1])
                from datetime import datetime
                with memory.get_conn() as conn:
                    conn.execute(
                        "UPDATE tasks SET text=?, updated_at=? WHERE id=?",
                        (text, datetime.now().isoformat(), task_id)
                    )

        elif len(parts) == 3 and parts[0] == "priority":
            task_id = int(parts[1])
            priority = int(parts[2])
            from datetime import datetime
            with memory.get_conn() as conn:
                conn.execute(
                    "UPDATE tasks SET priority=?, updated_at=? WHERE id=?",
                    (priority, datetime.now().isoformat(), task_id)
                )

        self.send_redirect()


def main():
    parser = argparse.ArgumentParser(description="Focus CLI web interface")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    memory.init_db()
    server = HTTPServer((args.host, args.port), FocusHandler)
    print(f"Focus web running at http://{args.host}:{args.port}")
    print("Access via Tailscale: http://<your-machine-name>:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
