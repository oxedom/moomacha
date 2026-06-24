"""One-off (operational): seed the cattle layer on prod Neon.

1. Provisions two brand-new outgoing-webhook pool bots via the Zulip admin API
   (no spare creds exist in .env; Claw/Echo/Bastion are registered agents, not
   reusable), then seeds them into PoolStore as `free` workers.
2. Creates two reusable archetypes: `meme-marketer` and `researcher`.

Idempotent guards:
- Provisioning is skipped if the pool already holds >= TARGET_FREE free bots.
- Each archetype is skipped if its name already exists.

Run from control-plane/ (reads ./.env):  uv run python scripts/seed_pool_and_archetypes.py
"""

import asyncio

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.schemas.archetype import ArchetypeDefinition
from control_plane.services.archetype_catalog import ArchetypeCatalog
from control_plane.services.crypto import SecretBox
from control_plane.services.pool_store import PoolStore
from control_plane.services.zulip_admin import ZulipAdminClient

TARGET_FREE = 2
CHANNELS = ["sandbox"]

POOL_BOTS = [
    ("Pool Worker 1", "pool-worker-1"),
    ("Pool Worker 2", "pool-worker-2"),
]

ARCHETYPES = [
    ArchetypeDefinition(
        name="meme-marketer",
        persona=(
            "You are a sharp social-media marketer who specializes in meme-driven "
            "campaigns. You produce meme CONCEPTS, captions, and short viral copy — "
            "you do not render images (no image tool exists). For each request, "
            "propose 2-3 concrete meme ideas: describe the visual/template, the "
            "top/bottom text, and why it lands for the target audience. Research "
            "current trends and references with the Tavily tools when useful. Keep "
            "it punchy and on-brand; minimal filler."
        ),
        model_id="gpt-4o",
        allowed_tools=["tavily_search", "tavily_extract", "read_topic", "read_channel"],
        runtime_kind="deepagents",
    ),
    ArchetypeDefinition(
        name="researcher",
        persona=(
            "You are a rigorous web researcher. You investigate the question, "
            "gather evidence with the Tavily tools, and report a concise, "
            "well-structured answer WITH citations (source titles + URLs). "
            "Distinguish facts from inference, flag uncertainty, and never "
            "fabricate sources."
        ),
        model_id="gpt-4o",
        allowed_tools=[
            "tavily_search",
            "tavily_extract",
            "tavily_research",
            "tavily_crawl",
            "read_topic",
            "read_channel",
        ],
        runtime_kind="deepagents",
    ),
]


async def main() -> None:
    settings = Settings()
    factory, engine = build_session_factory(settings.neon_database_url)
    pool = PoolStore(factory, SecretBox(settings.agent_fernet_key))
    catalog = ArchetypeCatalog(factory)

    # --- Pool bots -------------------------------------------------------
    free = await pool.count_free()
    print(f"pool: {free} free bot(s) currently.")
    if free >= TARGET_FREE:
        print(f"pool already has >= {TARGET_FREE} free bots; skipping provisioning.")
    elif not (settings.zulip_admin_email and settings.zulip_admin_api_key):
        print("WARNING: no admin creds in .env; cannot provision pool bots. Skipping.")
    else:
        base = (settings.public_base_url or settings.zulip_site).rstrip("/")
        payload_url = f"{base}/zulip/incoming"
        admin = ZulipAdminClient(
            site=settings.zulip_site,
            email=settings.zulip_admin_email,
            api_key=settings.zulip_admin_api_key,
        )
        for full_name, short_name in POOL_BOTS[: TARGET_FREE - free]:
            print(f"provisioning {full_name!r} (payload_url={payload_url}) ...")
            result = await admin.provision_bot(
                full_name=full_name,
                short_name=short_name,
                payload_url=payload_url,
                channels=CHANNELS,
            )
            if not result.outgoing_token:
                print(
                    f"  WARNING: no outgoing token captured for {result.bot_email}; "
                    "webhook token validation will fail until one is attached."
                )
            row = await pool.seed(
                zulip_bot_id=result.bot_id,
                zulip_bot_email=result.bot_email,
                api_key=result.api_key,
                outgoing_token=result.outgoing_token or "",
            )
            print(f"  seeded pool bot {result.bot_email} (id={row.id}, status={row.status})")

    # --- Archetypes ------------------------------------------------------
    for defn in ARCHETYPES:
        if await catalog.get_by_name(defn.name) is not None:
            print(f"archetype {defn.name!r} already exists; skipping.")
            continue
        saved = await catalog.create(defn)
        print(f"created archetype {saved.name!r} (model={saved.model_id}, tools={saved.allowed_tools})")

    # --- Summary ---------------------------------------------------------
    print("\n=== pool ===")
    for b in await pool.list_all():
        print(f"  {b.zulip_bot_email}  status={b.status}  current_name={b.current_name}")
    print("=== archetypes ===")
    for a in await catalog.list_all():
        print(f"  {a.name}  runtime={a.runtime_kind}  tools={a.allowed_tools}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
