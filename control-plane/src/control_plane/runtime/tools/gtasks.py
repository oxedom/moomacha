"""Google Tasks tools (in-process REST over the Tasks v1 API).

Shares the OAuth token with the Calendar tools via :class:`GoogleClient`. Task
lists map to the user's lists (My Tasks, Career, Personal, ...).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from control_plane.runtime.tools.google_api import GoogleClient
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult

_TASKS = "https://tasks.googleapis.com/tasks/v1"
OUTPUT_CAP = 8000


def _cap(value: str, limit: int = OUTPUT_CAP) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated to {limit} characters ..."


def _fmt_task(t: dict[str, Any]) -> str:
    box = "[x]" if t.get("status") == "completed" else "[ ]"
    line = f"- {box} {t.get('title') or '(untitled)'}"
    if t.get("due"):
        line += f"  (due {t['due']})"
    if t.get("notes"):
        line += f"  — {t['notes']}"
    if t.get("id"):
        line += f"  [id={t['id']}]"
    return line


# --- input models -------------------------------------------------------------


class ListTaskListsInput(BaseModel):
    pass


class ListTasksInput(BaseModel):
    list_id: str = Field(description="Task list id (from gtasks_list_task_lists).")
    show_completed: bool = Field(default=False, description="Include completed tasks.")
    due_min: str | None = Field(
        default=None, description="RFC3339 lower bound on due date (only tasks due on/after this)."
    )
    max_results: int = Field(default=100, ge=1, le=100)


class CreateTaskInput(BaseModel):
    list_id: str = Field(description="Task list id to add the task to.")
    title: str = Field(description="Task title.")
    notes: str | None = Field(default=None, description="Optional task notes/details.")
    due: str | None = Field(
        default=None, description="Optional RFC3339 due date (e.g. '2026-06-10T00:00:00Z'). Date-only."
    )


class CompleteTaskInput(BaseModel):
    list_id: str = Field(description="Task list id the task belongs to.")
    task_id: str = Field(description="Task id to mark completed.")


class UpdateTaskInput(BaseModel):
    list_id: str = Field(description="Task list id the task belongs to.")
    task_id: str = Field(description="Task id to update.")
    title: str | None = Field(default=None, description="New title.")
    notes: str | None = Field(default=None, description="New notes.")
    due: str | None = Field(default=None, description="New RFC3339 due date.")


class DeleteTaskInput(BaseModel):
    list_id: str = Field(description="Task list id the task belongs to.")
    task_id: str = Field(description="Task id to delete.")


# --- adapters -----------------------------------------------------------------


async def _list_task_lists(inp: ListTaskListsInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    data = await g.request("GET", f"{_TASKS}/users/@me/lists", params={"maxResults": 100})
    items = data.get("items", [])
    if not items:
        return ToolResult(ok=True, content="(no task lists)")
    lines = [f"- {t.get('title')}  [id={t.get('id')}]" for t in items]
    return ToolResult(ok=True, content=_cap("\n".join(lines)))


async def _list_tasks(inp: ListTasksInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    data = await g.request(
        "GET",
        f"{_TASKS}/lists/{inp.list_id}/tasks",
        params={
            "showCompleted": "true" if inp.show_completed else "false",
            "showHidden": "true" if inp.show_completed else "false",
            "dueMin": inp.due_min,
            "maxResults": inp.max_results,
        },
    )
    items = data.get("items", [])
    if not items:
        return ToolResult(ok=True, content="(no tasks)")
    return ToolResult(ok=True, content=_cap("\n".join(_fmt_task(t) for t in items)))


async def _create_task(inp: CreateTaskInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    body: dict[str, Any] = {"title": inp.title}
    if inp.notes:
        body["notes"] = inp.notes
    if inp.due:
        body["due"] = inp.due
    t = await g.request("POST", f"{_TASKS}/lists/{inp.list_id}/tasks", json=body)
    return ToolResult(ok=True, content=f"Created task:\n{_fmt_task(t)}")


async def _complete_task(inp: CompleteTaskInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    t = await g.request(
        "PATCH",
        f"{_TASKS}/lists/{inp.list_id}/tasks/{inp.task_id}",
        json={"status": "completed"},
    )
    return ToolResult(ok=True, content=f"Completed:\n{_fmt_task(t)}")


async def _update_task(inp: UpdateTaskInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    body: dict[str, Any] = {}
    if inp.title is not None:
        body["title"] = inp.title
    if inp.notes is not None:
        body["notes"] = inp.notes
    if inp.due is not None:
        body["due"] = inp.due
    if not body:
        return ToolResult(ok=False, content="Nothing to update (provide title, notes, or due).")
    t = await g.request("PATCH", f"{_TASKS}/lists/{inp.list_id}/tasks/{inp.task_id}", json=body)
    return ToolResult(ok=True, content=f"Updated:\n{_fmt_task(t)}")


async def _delete_task(inp: DeleteTaskInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    await g.request("DELETE", f"{_TASKS}/lists/{inp.list_id}/tasks/{inp.task_id}")
    return ToolResult(ok=True, content="Task deleted.")


_TOOLS = [
    ("gtasks_list_task_lists", "List the user's Google Task lists (name + id).", ListTaskListsInput, _list_task_lists),
    ("gtasks_list_tasks", "List tasks in a Google Task list (incomplete by default).", ListTasksInput, _list_tasks),
    ("gtasks_create_task", "Add a task to a Google Task list.", CreateTaskInput, _create_task),
    ("gtasks_complete_task", "Mark a Google task as completed.", CompleteTaskInput, _complete_task),
    ("gtasks_update_task", "Update a Google task's title, notes, or due date.", UpdateTaskInput, _update_task),
    ("gtasks_delete_task", "Delete a Google task.", DeleteTaskInput, _delete_task),
]


def register_gtasks_tools(registry: ToolRegistry, client: GoogleClient) -> None:
    for name, desc, model, fn in _TOOLS:
        registry.register(name, desc, model, (lambda f: lambda inp, ctx: f(inp, ctx, client))(fn))
