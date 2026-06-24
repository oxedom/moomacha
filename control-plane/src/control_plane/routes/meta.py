from fastapi import APIRouter


def build_meta_router(*, git_sha: str, version: str, started_at: str) -> APIRouter:
    """A version/identity endpoint so the *running* instance reports the commit it
    was built from. Lets us diff GitHub `main` against what the VPS/local container
    actually serves (the image bakes GIT_SHA at build time; see Dockerfile +
    scripts/box-redeploy.sh). `git_sha` is "unknown" when not baked (e.g. local dev).
    """
    router = APIRouter()

    @router.get("/version")
    async def version_info() -> dict[str, str]:
        return {"git_sha": git_sha, "version": version, "started_at": started_at}

    return router
