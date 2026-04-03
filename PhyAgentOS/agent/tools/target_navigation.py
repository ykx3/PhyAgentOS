"""Target navigation tool that dispatches lower-level visual navigation actions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PhyAgentOS.agent.tools.base import Tool
from PhyAgentOS.agent.tools.embodied import EmbodiedActionTool

if TYPE_CHECKING:
    from PhyAgentOS.embodiment_registry import EmbodimentRegistry


class TargetNavigationTool(Tool):
    """Dispatch lower-level target-label navigation without scene graph resolution."""

    def __init__(self, workspace: Path, action_tool: EmbodiedActionTool, registry: EmbodimentRegistry | None = None):
        self.workspace = workspace
        self.action_tool = action_tool
        self.registry = registry

    @property
    def name(self) -> str:
        return "target_navigation"

    @property
    def description(self) -> str:
        return "Navigate directly toward a visual target label using the lower-level target navigation stack."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "robot_id": {"type": "string"},
                "target_label": {"type": "string"},
                "detection_hint": {"type": "object"},
                "success_distance_m": {"type": "number", "minimum": 0.1},
                "success_heading_deg": {"type": "number", "minimum": 0},
                "control_mode": {"type": "string", "enum": ["preemptive", "blocking"]},
                "timeout_s": {"type": "number", "minimum": 1},
                "reasoning": {"type": "string"},
            },
            "required": ["robot_id", "target_label", "reasoning"],
        }

    async def execute(
        self,
        robot_id: str,
        target_label: str,
        reasoning: str,
        detection_hint: dict[str, Any] | None = None,
        success_distance_m: float | None = None,
        success_heading_deg: float | None = None,
        control_mode: str | None = None,
        timeout_s: float = 30.0,
    ) -> str:
        if not target_label.strip():
            return "Error: target_label is required"

        parameters: dict[str, Any] = {
            "robot_id": robot_id,
            "target_label": target_label,
            "timeout_s": timeout_s,
        }
        if detection_hint is not None:
            parameters["detection_hint"] = detection_hint
        if success_distance_m is not None:
            parameters["success_distance_m"] = success_distance_m
        if success_heading_deg is not None:
            parameters["success_heading_deg"] = success_heading_deg
        if control_mode is not None:
            parameters["control_mode"] = control_mode

        return await self.action_tool.execute(
            action_type="target_navigation",
            parameters=parameters,
            reasoning=reasoning,
        )
