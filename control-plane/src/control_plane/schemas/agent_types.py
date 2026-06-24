from typing import Literal

from pydantic import BaseModel, ConfigDict


AgentTypeLifecycle = Literal["current_slice", "planned_template"]


class AgentTypeRead(BaseModel):
    """Read-only description of an agent shape this system can or will spawn."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    lifecycle: AgentTypeLifecycle
    description: str
    runtime: str
    spawn_boundary: str
    isolation: str
    read_scope: str
    write_scope: str
    approval_policy: str
    capabilities: tuple[str, ...]
    source_refs: tuple[str, ...]
