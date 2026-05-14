"""HTTP server that renders /tmp/canary_replays/ as a side-by-side viewer.

Reads the filesystem live each request — no rebuild needed when you add
new replays. Single page: task list on the left, before/after images +
prompt/reply/tool_calls on the right.

Usage:
    python -m scripts.qa.serve_replay_viewer
    # then open http://127.0.0.1:8091/

Optional:
    REPLAY_DIR=/path/to/replays python -m scripts.qa.serve_replay_viewer
"""
from __future__ import annotations

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, unquote

REPLAY_DIR = Path(os.environ.get("REPLAY_DIR", "/tmp/canary_replays"))
PORT = int(os.environ.get("PORT", "8091"))


def list_tasks() -> list[str]:
    if not REPLAY_DIR.exists():
        return []
    return sorted([p.name for p in REPLAY_DIR.iterdir() if p.is_dir()])


def render_index(selected: str | None) -> str:
    tasks = list_tasks()
    if not tasks:
        return f"""<!doctype html><meta charset=utf-8>
<title>Canary Replay Viewer</title>
<body style="font-family:system-ui;padding:2rem;background:#111;color:#ddd">
<h1>No replays found</h1>
<p>Expected directory: <code>{REPLAY_DIR}</code></p>
<p>Run <code>python -m scripts.qa.replay_with_screenshots --tasks G-04</code> first.</p>
</body>"""

    if selected not in tasks:
        selected = tasks[0]

    task_dir = REPLAY_DIR / selected
    prompt = (task_dir / "prompt.txt").read_text() if (task_dir / "prompt.txt").exists() else ""
    reply = (task_dir / "reply.txt").read_text() if (task_dir / "reply.txt").exists() else ""
    tool_calls_raw = (
        (task_dir / "tool_calls.json").read_text()
        if (task_dir / "tool_calls.json").exists()
        else "[]"
    )
    try:
        tool_calls = json.loads(tool_calls_raw)
    except Exception:
        tool_calls = []

    # Format tool calls compactly
    tc_html = ""
    for i, tc in enumerate(tool_calls, 1):
        name = tc.get("tool", "?")
        args = tc.get("arguments", {})
        result = tc.get("result", {})
        rt = result.get("type", "?") if isinstance(result, dict) else "?"
        success = result.get("success") if isinstance(result, dict) else None
        success_marker = "✓" if success is True else ("✗" if success is False else "·")
        err = ""
        if isinstance(result, dict):
            err = result.get("error", "") or ""
        args_short = json.dumps(args, default=str)
        if len(args_short) > 200:
            args_short = args_short[:200] + "…"
        err_short = err[:300] + "…" if len(err) > 300 else err
        tc_html += f"""
<div class="tc">
  <div class="tc-head"><span class="ok-{success_marker}">{success_marker}</span>
    <strong>{i}. {name}</strong> <span class="dim">→ {rt}</span></div>
  <pre class="args">{args_short}</pre>
  {f'<pre class="err">{err_short}</pre>' if err_short else ''}
</div>"""

    task_links = "".join(
        f'<a href="/?task={t}" class="{"active" if t == selected else ""}">{t}</a>'
        for t in tasks
    )

    return f"""<!doctype html>
<html><head><meta charset=utf-8>
<title>Canary Replay — {selected}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, sans-serif; margin: 0; background: #0d1117; color: #c9d1d9; }}
  .layout {{ display: grid; grid-template-columns: 200px 1fr; height: 100vh; }}
  .sidebar {{ background: #161b22; border-right: 1px solid #30363d; overflow-y: auto; padding: 1rem 0; }}
  .sidebar h2 {{ font-size: 0.75rem; text-transform: uppercase; padding: 0 1rem; color: #8b949e; }}
  .sidebar a {{ display: block; padding: 0.5rem 1rem; color: #c9d1d9; text-decoration: none; border-left: 3px solid transparent; }}
  .sidebar a:hover {{ background: #1c2128; }}
  .sidebar a.active {{ background: #1f2937; border-left-color: #58a6ff; color: #58a6ff; }}
  .main {{ overflow-y: auto; padding: 1.5rem 2rem; }}
  h1 {{ margin: 0 0 1rem; font-size: 1.5rem; }}
  .images {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1.5rem; }}
  .img-cell {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 0.75rem; }}
  .img-cell h3 {{ margin: 0 0 0.5rem; font-size: 0.85rem; color: #8b949e; text-transform: uppercase; }}
  .img-cell img {{ width: 100%; height: auto; display: block; border-radius: 4px; background: #000; }}
  .panels {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1.5rem; }}
  .panel {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 0.75rem 1rem; }}
  .panel h3 {{ margin: 0 0 0.5rem; font-size: 0.85rem; color: #8b949e; text-transform: uppercase; }}
  .panel pre {{ white-space: pre-wrap; word-wrap: break-word; margin: 0; font-size: 0.85rem; line-height: 1.5; max-height: 300px; overflow-y: auto; }}
  .panel.prompt {{ border-left: 3px solid #d29922; }}
  .panel.reply {{ border-left: 3px solid #3fb950; }}
  .tools {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 0.75rem 1rem; }}
  .tools h3 {{ margin: 0 0 0.5rem; font-size: 0.85rem; color: #8b949e; text-transform: uppercase; }}
  .tc {{ margin-bottom: 0.75rem; padding-bottom: 0.5rem; border-bottom: 1px solid #21262d; }}
  .tc:last-child {{ border-bottom: none; }}
  .tc-head {{ font-size: 0.9rem; margin-bottom: 0.25rem; }}
  .tc pre {{ background: #0d1117; padding: 0.4rem 0.6rem; margin: 0.2rem 0; border-radius: 3px; font-size: 0.8rem; overflow-x: auto; }}
  .args {{ color: #8b949e; }}
  .err {{ color: #f85149; }}
  .dim {{ color: #8b949e; font-weight: normal; }}
  .ok-✓ {{ color: #3fb950; font-weight: bold; }}
  .ok-✗ {{ color: #f85149; font-weight: bold; }}
  .ok-· {{ color: #8b949e; }}
</style></head>
<body>
<div class="layout">
  <div class="sidebar">
    <h2>Tasks ({len(tasks)})</h2>
    {task_links}
  </div>
  <div class="main">
    <h1>{selected}</h1>
    <div class="images">
      <div class="img-cell"><h3>Before</h3><img src="/img/{selected}/before.png" alt="before"></div>
      <div class="img-cell"><h3>After</h3><img src="/img/{selected}/after.png" alt="after"></div>
    </div>
    <div class="panels">
      <div class="panel prompt"><h3>Prompt</h3><pre>{prompt}</pre></div>
      <div class="panel reply"><h3>Agent reply</h3><pre>{reply}</pre></div>
    </div>
    <div class="tools">
      <h3>Tool calls ({len(tool_calls)})</h3>
      {tc_html or '<em>no tool calls</em>'}
    </div>
  </div>
</div>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # silence default access log
        pass

    def do_GET(self):
        u = urlparse(self.path)
        path = unquote(u.path)

        if path == "/" or path.startswith("/?"):
            from urllib.parse import parse_qs
            qs = parse_qs(u.query)
            selected = qs.get("task", [None])[0]
            html = render_index(selected)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
            return

        if path.startswith("/img/"):
            rel = path[len("/img/"):]
            f = (REPLAY_DIR / rel).resolve()
            try:
                f.relative_to(REPLAY_DIR.resolve())
            except ValueError:
                self.send_response(403)
                self.end_headers()
                return
            if not f.exists():
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(f.read_bytes())
            return

        if path.startswith("/video/"):
            rel = path[len("/video/"):]
            f = (REPLAY_DIR / rel).resolve()
            try:
                f.relative_to(REPLAY_DIR.resolve())
            except ValueError:
                self.send_response(403)
                self.end_headers()
                return
            if not f.exists():
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(f.read_bytes())
            return

        self.send_response(404)
        self.end_headers()


def main() -> int:
    print(f"Replay viewer reading from: {REPLAY_DIR}")
    print(f"Open: http://127.0.0.1:{PORT}/")
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
