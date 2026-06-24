from fastapi import APIRouter
from pydantic import BaseModel

from control_plane.services.pool_store import PoolStore


class PoolBotSeed(BaseModel):
    zulip_bot_id: int
    zulip_bot_email: str
    zulip_api_key: str
    zulip_outgoing_token: str


def build_pool_router(pool_store: PoolStore) -> APIRouter:
    router = APIRouter(prefix="/pool")

    @router.post("/bots", status_code=201)
    async def seed_pool_bot(body: PoolBotSeed) -> dict:
        await pool_store.seed(
            zulip_bot_id=body.zulip_bot_id,
            zulip_bot_email=body.zulip_bot_email,
            api_key=body.zulip_api_key,
            outgoing_token=body.zulip_outgoing_token,
        )
        return {"status": "seeded"}

    @router.get("/bots")
    async def list_pool_bots() -> list[dict]:
        bots = await pool_store.list_all()
        return [
            {
                "id": str(b.id),
                "email": b.zulip_bot_email,
                "status": b.status,
                "current_name": b.current_name,
            }
            for b in bots
        ]

    return router
