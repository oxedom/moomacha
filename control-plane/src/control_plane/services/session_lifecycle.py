from control_plane.db.tables import PoolBotRow
from control_plane.services.pool_store import PoolStore
from control_plane.services.session_store import SessionStore


async def reclaim_for_capacity(pool: PoolStore, sessions: SessionStore) -> PoolBotRow | None:
    """Free one pool bot under pressure: close the oldest dormant session and
    release its bot. Returns the freed PoolBotRow, or None if nothing to reclaim."""
    victim = await sessions.oldest_dormant()
    if victim is None:
        return None
    await sessions.close(victim.id)
    if victim.pool_bot_id is not None:
        await pool.release(victim.pool_bot_id)
        return await pool.get(victim.pool_bot_id)
    return None
