"""One-off: grant can_exec to the Bastion agent for live exec e2e.

Ensures the agents.can_exec column exists on Neon (ADD COLUMN IF NOT EXISTS,
defensive vs create_all not adding columns to existing tables), then sets
can_exec=True on the is_bastion row directly via the ORM (AgentUpdate has no
can_exec field). Idempotent; prints the bastion's identity for the e2e.
"""

import asyncio

from sqlalchemy import select, text

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.db.tables import AgentRow


async def main() -> None:
    settings = Settings()
    factory, _ = build_session_factory(settings.neon_database_url)
    async with factory() as session:
        await session.execute(
            text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS can_exec BOOLEAN DEFAULT FALSE")
        )
        await session.commit()

        rows = (await session.execute(select(AgentRow).where(AgentRow.is_bastion.is_(True)))).scalars().all()
        if not rows:
            print("NO bastion row found (is_bastion=True). Nothing changed.")
            return
        for row in rows:
            print(
                f"bastion: name={row.name!r} id={row.id} bot_email={row.zulip_bot_email!r} "
                f"can_exec(before)={row.can_exec} readable_channels={row.readable_channels}"
            )
            row.can_exec = True
        await session.commit()

        rows = (await session.execute(select(AgentRow).where(AgentRow.is_bastion.is_(True)))).scalars().all()
        for row in rows:
            print(f"AFTER: name={row.name!r} can_exec={row.can_exec}")


if __name__ == "__main__":
    asyncio.run(main())
