from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from control_plane.runtime.tools.runtime import ToolContext, ToolResult

logger = logging.getLogger("control_plane")

Adapter = Callable[["BaseModel", "ToolContext"], Awaitable["ToolResult"]]


@dataclass
class ToolEntry:
    description: str
    input_model: type[BaseModel]
    adapter: Adapter
    management: bool = False
    requires_exec: bool = False


class ToolRegistry:
    """Global registration table for tools. Built once at app startup."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register(
        self,
        name: str,
        description: str,
        input_model: type[BaseModel],
        adapter: Adapter,
        *,
        management: bool = False,
        requires_exec: bool = False,
    ) -> None:
        self._tools[name] = ToolEntry(
            description, input_model, adapter, management, requires_exec
        )

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def _resolve_names(
        self, allowed_tools: list[str], is_bastion: bool, can_exec: bool
    ) -> list[str]:
        """Expand allowed_tools with the privilege tools an agent implicitly gets.

        Management tools are added for the bastion and exec tools for a can_exec
        agent, regardless of allowed_tools (privilege follows the flag, not the
        model-editable list). Shared by build_schemas (what the model is bound to)
        and describe_tools (what the model is told it has) so the two never drift.

        Privilege follows the flag in BOTH directions: a management/exec tool that
        appears in the model-editable allowed_tools is dropped unless the agent
        actually holds the corresponding flag, so naming a privileged tool in
        allowed_tools can never grant it. Unregistered names pass through and are
        skipped downstream.
        """
        names: list[str] = []
        for name in allowed_tools:
            entry = self._tools.get(name)
            if entry is not None and not self._permitted_by_flags(
                entry, is_bastion=is_bastion, can_exec=can_exec
            ):
                continue
            names.append(name)
        if is_bastion:
            names += [n for n, e in self._tools.items() if e.management and n not in names]
        if can_exec:
            names += [n for n, e in self._tools.items() if e.requires_exec and n not in names]
        return names

    @staticmethod
    def _permitted_by_flags(
        entry: ToolEntry, *, is_bastion: bool = False, can_exec: bool = False
    ) -> bool:
        """Whether an agent with these flags may have this tool at all.

        Management tools require the bastion flag; exec tools require can_exec.
        Used both to filter allowed_tools (above) and as defense-in-depth at
        execution time (ToolRuntime).
        """
        if entry.management and not is_bastion:
            return False
        if entry.requires_exec and not can_exec:
            return False
        return True

    def build_schemas(
        self, allowed_tools: list[str], is_bastion: bool = False, can_exec: bool = False
    ) -> list[dict]:
        """Return OpenAI tool schemas for the agent's allowed tools.

        Names not registered are skipped (logged at debug), so a stale
        allowed_tools entry can never break a turn.
        """
        schemas: list[dict] = []
        for name in self._resolve_names(allowed_tools, is_bastion, can_exec):
            entry = self._tools.get(name)
            if entry is None:
                logger.debug("allowed tool %r is not registered; skipping", name)
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": entry.description,
                        "parameters": entry.input_model.model_json_schema(),
                    },
                }
            )
        return schemas

    def describe_tools(
        self, allowed_tools: list[str], is_bastion: bool = False, can_exec: bool = False
    ) -> list[tuple[str, str]]:
        """Return (name, description) pairs for the tools an agent actually has.

        Same name resolution as build_schemas — including bastion management and
        exec tools — so a textual tool list injected into the prompt matches the
        tools bound to the model. Unregistered names are skipped.
        """
        pairs: list[tuple[str, str]] = []
        for name in self._resolve_names(allowed_tools, is_bastion, can_exec):
            entry = self._tools.get(name)
            if entry is None:
                continue
            pairs.append((name, entry.description))
        return pairs
