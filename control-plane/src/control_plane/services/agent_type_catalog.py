from control_plane.schemas.agent_types import AgentTypeRead


_AGENT_TYPES: tuple[AgentTypeRead, ...] = (
    AgentTypeRead(
        id="strands_context_responder",
        name="DB-registered Strands context responder",
        lifecycle="current_slice",
        description=(
            "The currently planned executable agent shape: a registered Zulip bot "
            "whose persona and model live in Postgres, invoked by an @-mention."
        ),
        runtime="Strands Agents SDK with OpenAIModel for this implementation slice.",
        spawn_boundary=(
            "In-process asyncio worker now; the JobQueue and turn-loop seams let a "
            "later slice swap in a subprocess, container, or microVM boundary."
        ),
        isolation="No tools in this slice, so there is no arbitrary host execution path.",
        read_scope=(
            "Recent messages from the invoking Zulip channel/topic, fetched with "
            "the agent bot's own credentials."
        ),
        write_scope=(
            "Posts a working placeholder and edits that message into the final "
            "reply in the same Zulip topic."
        ),
        approval_policy="No approval gates in this slice because the agent has no tools.",
        capabilities=(
            "persona-driven replies",
            "recent topic context",
            "per-agent Zulip bot identity",
            "model override per agent",
        ),
        source_refs=(
            "docs/superpowers/specs/2026-05-23-execution-backbone-design.md",
            "docs/superpowers/plans/2026-05-23-execution-backbone.md",
        ),
    ),
    AgentTypeRead(
        id="read_only_researcher",
        name="Read-only researcher",
        lifecycle="planned_template",
        description=(
            "Research and synthesis agent with no autonomous write tools. Intended "
            "for answering questions from allowed channel/topic context."
        ),
        runtime="Future permissioned Strands agent.",
        spawn_boundary="One process per invocation in v1, with a future isolation seam.",
        isolation="Read-only tools only; no shell, package install, or arbitrary file IO.",
        read_scope="Allowed channels, allowed skills, topic reads, and memory reads.",
        write_scope="May post replies only where its permissions allow.",
        approval_policy="No approval needed for read-only work.",
        capabilities=(
            "summarization",
            "cross-topic lookup through scoped tools",
            "memory query",
            "skill loading from allowlisted skills",
        ),
        source_refs=("t.md#default-permission-templates",),
    ),
    AgentTypeRead(
        id="supervised_devops",
        name="Supervised devops",
        lifecycle="planned_template",
        description=(
            "Operations agent allowed to propose or perform infrastructure changes "
            "only through mediated tools and human approval gates."
        ),
        runtime="Future permissioned Strands agent.",
        spawn_boundary="One process per invocation in v1, with a future isolation seam.",
        isolation=(
            "Write-capable tools must enforce network, filesystem, and approval "
            "policy outside the prompt."
        ),
        read_scope="Allowed ops channels, explicit tools, and scoped memory.",
        write_scope="Approved infrastructure or service actions through control-plane tools.",
        approval_policy="Most write tools require human approval before execution.",
        capabilities=(
            "service inspection through approved tools",
            "deploy or config proposals",
            "incident notes",
            "human-gated remediation",
        ),
        source_refs=("t.md#default-permission-templates",),
    ),
    AgentTypeRead(
        id="autonomous_pr_maker",
        name="Autonomous PR maker",
        lifecycle="planned_template",
        description=(
            "Development agent that can prepare code changes autonomously while "
            "requiring approval for publishing actions."
        ),
        runtime="Future permissioned Strands agent.",
        spawn_boundary="One process per invocation in v1, with a future isolation seam.",
        isolation=(
            "Filesystem access is scoped to the assigned workspace; external side "
            "effects go through allowlisted tools."
        ),
        read_scope="Assigned repository/workspace, readable skills, and allowed chat context.",
        write_scope="Local workspace edits; push and PR creation are gated.",
        approval_policy="Approval required for push and pull-request creation.",
        capabilities=(
            "code edits",
            "test execution through mediated tools",
            "commit preparation",
            "approval-gated PR creation",
        ),
        source_refs=("t.md#default-permission-templates",),
    ),
    AgentTypeRead(
        id="intern",
        name="Intern",
        lifecycle="planned_template",
        description=(
            "Low-privilege assistant intended for constrained read-only or drafting "
            "work in development channels."
        ),
        runtime="Future permissioned Strands agent.",
        spawn_boundary="One process per invocation in v1, with a future isolation seam.",
        isolation="Read-only by default with narrow channel and social permissions.",
        read_scope="Only explicitly allowed development channels and skills.",
        write_scope="Can only post in allowed dev channels.",
        approval_policy="No write tools; cannot tag other agents.",
        capabilities=(
            "drafting",
            "summarization",
            "low-risk triage",
            "question answering from scoped context",
        ),
        source_refs=("t.md#default-permission-templates",),
    ),
)


def list_agent_types() -> list[AgentTypeRead]:
    return list(_AGENT_TYPES)


def get_agent_type(agent_type_id: str) -> AgentTypeRead | None:
    return next((item for item in _AGENT_TYPES if item.id == agent_type_id), None)
