from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from control_plane.services.browser_goal_runner import BrowserGoalRunner


class BrowserGoalStartRequest(BaseModel):
    goal: str = Field(min_length=1, description="Goal for the browser worker to pursue.")
    url: str | None = Field(default=None, description="Optional URL to open before planning.")
    model_id: str | None = Field(default=None, description="Optional model override.")
    max_steps: int = Field(default=20, ge=1, le=100)
    headed: bool = True
    persistent: bool = True


class BrowserGoalSteerRequest(BaseModel):
    message: str = Field(min_length=1, description="Human steering message for the run.")


class BrowserGoalStopRequest(BaseModel):
    close_browser: bool = Field(default=False, description="Also close the Playwright page.")


def build_browser_goals_router(runner: BrowserGoalRunner) -> APIRouter:
    router = APIRouter(prefix="/browser-goals", tags=["browser-goals"])

    @router.post("", status_code=201)
    async def start_goal(req: BrowserGoalStartRequest) -> dict:
        run = await runner.start(
            goal=req.goal,
            url=req.url,
            model_id=req.model_id,
            max_steps=req.max_steps,
            headed=req.headed,
            persistent=req.persistent,
        )
        return run.view()

    @router.get("")
    async def list_goals() -> list[dict]:
        return [run.view() for run in runner.list()]

    @router.get("/{run_id}")
    async def get_goal(run_id: str) -> dict:
        run = runner.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="browser goal not found")
        return run.view()

    @router.post("/{run_id}/steer")
    async def steer_goal(run_id: str, req: BrowserGoalSteerRequest) -> dict:
        run = await runner.steer(run_id, req.message)
        if run is None:
            raise HTTPException(status_code=404, detail="browser goal not found")
        return run.view()

    @router.post("/{run_id}/pause")
    async def pause_goal(run_id: str) -> dict:
        run = await runner.pause(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="browser goal not found")
        return run.view()

    @router.post("/{run_id}/resume")
    async def resume_goal(run_id: str) -> dict:
        run = await runner.resume(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="browser goal not found")
        return run.view()

    @router.post("/{run_id}/stop")
    async def stop_goal(run_id: str, req: BrowserGoalStopRequest | None = None) -> dict:
        run = await runner.stop(run_id, close_browser=bool(req and req.close_browser))
        if run is None:
            raise HTTPException(status_code=404, detail="browser goal not found")
        return run.view()

    return router
