"""Live e2e of the DB-backed substrate against real Neon: knowledge artifacts +
skills + the deepagents skill file map. Seeds clearly-marked probe rows and
deletes them at the end. Run from control-plane/:
    uv run python scripts/e2e_db_tools_live.py
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from sqlalchemy import text

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.runtime.runners.deepagents_runner import resolve_db_skills
from control_plane.runtime.tools.knowledge_artifacts import register_knowledge_artifact_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime
from control_plane.services.knowledge_artifact_store import KnowledgeArtifactStore
from control_plane.services.skill_catalog import SkillCatalog

TAG = uuid.uuid4().hex[:8]
ART = f"probe-artifact-{TAG}"
ART2 = f"probe-secret-{TAG}"
SKILL = f"probe-skill-{TAG}"
OK, NO = "\033[92m✓\033[0m", "\033[91m✗\033[0m"


def check(label, cond, all_ok):
    print(f"  {OK if cond else NO} {label}")
    return all_ok and cond


async def main() -> None:
    s = Settings()
    factory, engine = build_session_factory(s.neon_database_url)
    artifacts = KnowledgeArtifactStore(factory)
    skills = SkillCatalog(factory)
    ok = True
    try:
        print(f"\nprobe tag={TAG}\n")

        # --- Artifacts ---
        bound = await artifacts.upsert(name=ART, body="# Probe\nthe onboarding body line")
        await artifacts.upsert(name=ART2, body="# Secret\nunbound, must not appear")
        reg = ToolRegistry()
        register_knowledge_artifact_tools(reg, artifacts)
        rt = ToolRuntime(reg)
        agent = SimpleNamespace(id="probe", allowed_tools=["list_artifacts", "read_artifact"],
                                knowledge_artifact_ids=[str(bound.id)])
        ctx = ToolContext(agent=agent, zulip=None, channel="c", topic="t")

        print("knowledge artifacts (live Neon):")
        lst = await rt.execute("list_artifacts", "{}", ctx)
        ok = check("list shows bound artifact", ART in lst.content, ok)
        ok = check("list hides unbound artifact", ART2 not in lst.content, ok)
        rd = await rt.execute("read_artifact", f'{{"name": "{ART}"}}', ctx)
        ok = check("read bound artifact returns body", "onboarding body" in rd.content, ok)
        rd2 = await rt.execute("read_artifact", f'{{"name": "{ART2}"}}', ctx)
        ok = check("read unbound artifact refused", rd2.ok is False and "not available" in rd2.content.lower(), ok)

        # --- Skills ---
        print("skills (live Neon):")
        await skills.upsert(name=SKILL, body="---\nname: probe\ndescription: x\n---\nbody",
                            model_era="opus-4.x")
        loaded = await skills.load(names=[SKILL], model_era="opus-4.x")
        ok = check("era-matching skill loads", [r.name for r in loaded] == [SKILL], ok)
        mismatched = await skills.load(names=[SKILL], model_era="gpt-4o")
        ok = check("era-mismatched skill skipped", mismatched == [], ok)
        files = await resolve_db_skills(skills, names=[SKILL], model_id="claude-opus-4-7")
        key = f"/skills/{SKILL}/SKILL.md"
        ok = check("resolve_db_skills produces /skills/<name>/SKILL.md key", key in files, ok)
        ok = check("skill body wrapped (FileData dict)", isinstance(files.get(key), dict), ok)

        print(f"\n{'ALL DB-TOOL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}")
    finally:
        # cleanup probe rows
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM knowledge_artifacts WHERE name IN (:a,:b)"),
                               {"a": ART, "b": ART2})
            await conn.execute(text("DELETE FROM skills WHERE name = :n"), {"n": SKILL})
        print("cleaned up probe rows")
        await engine.dispose()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
