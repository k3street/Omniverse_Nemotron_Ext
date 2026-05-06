"""Tiny FastAPI server that renders QA task .md files as HTML.

Run:
  python -m scripts.qa.task_browser

Then open http://localhost:8090 in a small browser window.

Designed for compact / single-column reading. Auto-discovers tasks in
docs/qa/tasks/*.md, lists them in the sidebar, renders the selected
task's markdown body. Refresh browser after editing a spec — no
caching.
"""
from __future__ import annotations
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

try:
    import markdown
except ImportError:
    markdown = None  # graceful fallback to <pre> if package missing

ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "docs" / "qa" / "tasks"

app = FastAPI(title="QA Task Browser")


def _list_tasks() -> list[tuple[str, str]]:
    """Return [(task_id, title)] sorted by task_id."""
    out = []
    for p in sorted(TASKS_DIR.glob("*.md")):
        tid = p.stem
        # First H1 line for title display
        title = tid
        try:
            for line in p.read_text().splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        except Exception:
            pass
        out.append((tid, title))
    return out


def _render_markdown(md: str) -> str:
    if markdown is None:
        # Minimal fallback — just escape and wrap in pre
        from html import escape
        return f"<pre>{escape(md)}</pre>"
    return markdown.markdown(
        md,
        extensions=["fenced_code", "tables", "toc", "sane_lists"],
    )


_BASE_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
    margin: 0;
    background: #1c1c1c;
    color: #d0d0d0;
    font: 13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    display: flex;
    flex-direction: column;
    height: 100vh;
}
header {
    background: #161616;
    border-bottom: 1px solid #2a2a2a;
    padding: 6px 12px;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
}
header label {
    font-size: 11px;
    text-transform: uppercase;
    color: #888;
    letter-spacing: 0.5px;
}
header select {
    flex: 1;
    background: #232323;
    color: #e0e0e0;
    border: 1px solid #2a2a2a;
    border-radius: 3px;
    padding: 4px 6px;
    font-size: 12px;
    font-family: inherit;
    max-width: 400px;
}
header select:focus { outline: 1px solid #4a6dab; }
header .count {
    font-size: 11px;
    color: #666;
}
main {
    flex: 1;
    overflow-y: auto;
    padding: 14px 18px;
}
main h1 { font-size: 16px; margin: 0 0 10px; color: #f0f0f0; }
main h2 { font-size: 14px; margin: 14px 0 6px; color: #d8d8d8; border-bottom: 1px solid #2a2a2a; padding-bottom: 2px; }
main h3 { font-size: 13px; margin: 10px 0 4px; color: #c8c8c8; }
main p { margin: 6px 0; }
main ul, main ol { margin: 4px 0 8px 18px; padding: 0; }
main li { margin: 1px 0; }
main code {
    background: #232323;
    padding: 1px 5px;
    border-radius: 3px;
    font-family: ui-monospace, "JetBrains Mono", monospace;
    font-size: 12px;
}
main pre {
    background: #0f0f0f;
    border: 1px solid #2a2a2a;
    padding: 8px 10px;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 11.5px;
    line-height: 1.45;
}
main pre code { background: transparent; padding: 0; }
main strong { color: #f0f0f0; }
main blockquote {
    border-left: 3px solid #4a6dab;
    margin: 6px 0;
    padding: 2px 10px;
    background: #1f2530;
    color: #c0c0c0;
}
main table { border-collapse: collapse; margin: 8px 0; font-size: 12px; }
main th, main td { border: 1px solid #2a2a2a; padding: 4px 8px; }
main th { background: #232323; }
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    tasks = _list_tasks()
    if tasks:
        return RedirectResponse(f"/task/{tasks[0][0]}")
    return HTMLResponse(f"<body style='background:#1c1c1c;color:#888;font-family:sans-serif;padding:20px'>No tasks found in {TASKS_DIR}</body>")


@app.get("/task/{task_id}", response_class=HTMLResponse)
async def view_task(task_id: str):
    if not re.match(r"^[A-Za-z0-9_-]+$", task_id):
        raise HTTPException(400, "bad task_id")
    p = TASKS_DIR / f"{task_id}.md"
    if not p.exists():
        raise HTTPException(404, f"{task_id} not found")
    body_html = _render_markdown(p.read_text())

    tasks = _list_tasks()
    options = "".join(
        f'<option value="{tid}"{" selected" if tid == task_id else ""}>'
        f'{tid} — {title.replace(tid, "").strip(" -—:")[:60] or title}</option>'
        for tid, title in tasks
    )

    return HTMLResponse(f"""<!doctype html><html><head>
<meta charset="utf-8">
<title>{task_id}</title>
<style>{_BASE_CSS}</style>
</head><body>
<header>
<label>Task</label>
<select onchange="if(this.value) window.location='/task/'+this.value">
{options}
</select>
<span class="count">{len(tasks)} total</span>
</header>
<main>{body_html}</main>
</body></html>""")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8090, log_level="warning")
