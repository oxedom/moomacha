import logging

from control_plane.services.pool_store import PoolStore
from control_plane.services.session_store import SessionStore

logger = logging.getLogger("control_plane")


async def recover_pool_consistency(pool_store: PoolStore, session_store: SessionStore) -> None:
    """Idempotent startup reconciliation of the pool<->session invariant.

    Repairs state a crash mid-spin_up_session can leave behind:
      1. A 'provisioning' session whose birth never reached mark_live.
      2. A 'leased' pool bot whose session is missing / closed / no longer points back.

    Safe to run repeatedly: released bots are already free, closed sessions stay closed.
    """
    # 1. Abandoned provisioning sessions: release any bot bound to them, close the row.
    for s in await session_store.list_by_state("provisioning"):
        await pool_store.release_for_session(s.id)
        await session_store.close(s.id)
        logger.info("orphan_recovery: closed abandoned provisioning session %s", s.id)

    # 2. Orphan leases (belt-and-suspenders for any other inconsistency).
    for bot in await pool_store.find_leased():
        sess = await session_store.get(bot.current_session_id) if bot.current_session_id else None
        if sess is None or sess.state == "closed" or sess.pool_bot_id != bot.id:
            await pool_store.release(bot.id)
            logger.info("orphan_recovery: released orphan lease on bot %s", bot.id)
