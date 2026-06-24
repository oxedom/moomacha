from html import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from control_plane.schemas.agent_types import AgentTypeRead
from control_plane.services.agent_type_catalog import get_agent_type, list_agent_types


def build_agent_types_router() -> APIRouter:
    router = APIRouter(tags=["agent-types"])

    @router.get("/api/agent-types", response_model=list[AgentTypeRead])
    async def agent_types() -> list[AgentTypeRead]:
        return list_agent_types()

    @router.get("/api/agent-types/{agent_type_id}", response_model=AgentTypeRead)
    async def agent_type(agent_type_id: str) -> AgentTypeRead:
        item = get_agent_type(agent_type_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Unknown agent type")
        return item

    @router.get("/dev/agent-types", response_class=HTMLResponse)
    async def agent_types_dashboard() -> str:
        return render_agent_types_dashboard(list_agent_types())

    return router


def render_agent_types_dashboard(agent_types: list[AgentTypeRead]) -> str:
    current = [item for item in agent_types if item.lifecycle == "current_slice"]
    planned = [item for item in agent_types if item.lifecycle == "planned_template"]
    rows = "\n".join(_render_row(item) for item in agent_types)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Types</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f7f4;
      --text: #1b1c1d;
      --muted: #62666d;
      --line: #d8d7d0;
      --band: #ffffff;
      --accent: #0f766e;
      --planned: #5b5f6b;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #111315;
        --text: #f2f2ee;
        --muted: #a9adb4;
        --line: #2c3035;
        --band: #181b1f;
        --accent: #2dd4bf;
        --planned: #b7bbc4;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(1180px, calc(100vw - 32px));
      margin: 28px auto 48px;
    }}
    header {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 24px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 18px;
      margin-bottom: 18px;
    }}
    h1 {{
      font-size: 24px;
      margin: 0 0 6px;
      letter-spacing: 0;
    }}
    p {{
      margin: 0;
      color: var(--muted);
    }}
    .summary {{
      display: flex;
      gap: 18px;
      color: var(--muted);
      white-space: nowrap;
    }}
    .summary strong {{
      display: block;
      color: var(--text);
      font-size: 20px;
      font-weight: 650;
    }}
    .table-wrap {{
      overflow-x: auto;
      background: var(--band);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    table {{
      border-collapse: collapse;
      min-width: 980px;
      width: 100%;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 12px 14px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      text-transform: uppercase;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .id {{
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      margin-top: 3px;
    }}
    .badge {{
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      color: var(--planned);
      white-space: nowrap;
    }}
    .badge-current {{
      color: var(--accent);
      border-color: color-mix(in srgb, var(--accent), var(--line) 55%);
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
    }}
    code {{
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }}
    @media (max-width: 720px) {{
      main {{ width: min(100vw - 20px, 1180px); margin-top: 18px; }}
      header {{ display: block; }}
      .summary {{ margin-top: 16px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Agent Types</h1>
        <p>Read-only catalog of the agent shapes in the current orchestration plan.</p>
      </div>
      <div class="summary" aria-label="Summary">
        <span><strong>{len(current)}</strong> current</span>
        <span><strong>{len(planned)}</strong> planned</span>
        <span><strong>{len(agent_types)}</strong> total</span>
      </div>
    </header>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Status</th>
            <th>Runtime</th>
            <th>Scopes</th>
            <th>Capabilities</th>
          </tr>
        </thead>
        <tbody>
{rows}
        </tbody>
      </table>
    </div>
  </main>
</body>
</html>"""


def _render_row(item: AgentTypeRead) -> str:
    lifecycle_label = "current" if item.lifecycle == "current_slice" else "planned"
    badge_class = "badge badge-current" if item.lifecycle == "current_slice" else "badge"
    capabilities = "".join(f"<li>{escape(capability)}</li>" for capability in item.capabilities)
    return f"""          <tr>
            <td>
              <strong>{escape(item.name)}</strong>
              <div class="id">{escape(item.id)}</div>
              <p>{escape(item.description)}</p>
            </td>
            <td><span class="{badge_class}">{lifecycle_label}</span></td>
            <td>{escape(item.runtime)}<br><code>{escape(item.spawn_boundary)}</code></td>
            <td>
              <strong>Read:</strong> {escape(item.read_scope)}<br>
              <strong>Write:</strong> {escape(item.write_scope)}<br>
              <strong>Approval:</strong> {escape(item.approval_policy)}
            </td>
            <td><ul>{capabilities}</ul></td>
          </tr>"""
