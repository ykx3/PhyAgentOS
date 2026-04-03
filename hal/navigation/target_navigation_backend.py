"""Adapter layer that embeds navigation_sdk into OEA drivers."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def _ensure_navigation_sdk_importable() -> None:
    import sys

    sdk_root = Path(__file__).resolve().parents[3] / "navigation_sdk"
    if sdk_root.exists():
        sdk_root_str = str(sdk_root)
        if sdk_root_str not in sys.path:
            sys.path.insert(0, sdk_root_str)


def _import_navigation_sdk() -> dict[str, Any]:
    _ensure_navigation_sdk_importable()
    from navigation_mcp.bridge import Go2BridgeConfig, Go2MoveBridge, SimulatedRobotBridge
    from navigation_mcp.models import NavPhase, NavigationConfig, Observation
    from navigation_mcp.navigator import NavigationEngine

    return {
        "Go2BridgeConfig": Go2BridgeConfig,
        "Go2MoveBridge": Go2MoveBridge,
        "SimulatedRobotBridge": SimulatedRobotBridge,
        "NavPhase": NavPhase,
        "NavigationConfig": NavigationConfig,
        "NavigationEngine": NavigationEngine,
        "Observation": Observation,
    }


class TargetNavigationBackend:
    """Embed the navigation_sdk engine while keeping OEA runtime semantics."""

    def __init__(self, backend_mode: str = "mock", **config: Any):
        self.backend_mode = backend_mode
        self.config = dict(config)
        self._sdk: dict[str, Any] | None = None
        self._bridge: Any = None
        self._engine: Any = None
        self._last_status: dict[str, Any] = {}
        self._connected = False

    def connect(self) -> bool:
        if self._connected:
            return True
        sdk = self._sdk_api()
        if self.backend_mode == "real":
            cfg = sdk["Go2BridgeConfig"](**self._bridge_config_kwargs())
            self._bridge = sdk["Go2MoveBridge"](cfg)
        else:
            self._bridge = sdk["SimulatedRobotBridge"]()
        self._engine = sdk["NavigationEngine"](self._bridge)
        self._connected = True
        return True

    def disconnect(self) -> None:
        if not self._connected:
            return
        bridge = self._bridge
        if bridge is not None:
            stop_remote = getattr(bridge, "stop_remote_services", None)
            if callable(stop_remote):
                try:
                    stop_remote()
                except Exception:
                    pass
            motion_server = getattr(bridge, "motion_server", None)
            if motion_server is not None:
                try:
                    motion_server.stop()
                except Exception:
                    pass
            receiver = getattr(bridge, "receiver", None)
            if receiver is not None:
                try:
                    receiver.stop()
                except Exception:
                    pass
        self._connected = False

    def health_check(self) -> dict[str, Any]:
        if not self._connected:
            return {"connected": False, "status": "disconnected"}
        if self.backend_mode == "real" and self._bridge is not None:
            describe = getattr(self._bridge, "describe", None)
            if callable(describe):
                snapshot = describe()
                return {
                    "connected": bool(snapshot.get("motion_connected")),
                    "status": "connected" if snapshot.get("motion_connected") else "degraded",
                    "details": snapshot,
                }
        return {"connected": True, "status": "connected"}

    def run_navigation(self, params: dict[str, Any]) -> dict[str, Any]:
        self.connect()
        assert self._engine is not None
        target_label = str(params.get("target_label", "")).strip()
        if not target_label:
            raise ValueError("target_label is required")
        result = self._engine.set_target(
            target_label=target_label,
            success_distance_m=params.get("success_distance_m"),
            success_heading_deg=params.get("success_heading_deg"),
            control_mode=params.get("control_mode"),
            detection_hint=params.get("detection_hint"),
        )
        timeout_s = float(params.get("timeout_s", 30.0))
        result = self._engine.run_until_done(timeout_s=timeout_s, step_delay_s=0.0)
        if result.get("phase") == "searching":
            result = {
                **result,
                "phase": "not_found",
                "message": f"target not found before timeout ({timeout_s:.1f}s)",
            }
        elif result.get("phase") == "tracking":
            result = {
                **result,
                "phase": "blocked",
                "message": f"navigation timed out while tracking target ({timeout_s:.1f}s)",
            }
        self._last_status = result
        return result

    def stop(self) -> dict[str, Any]:
        if not self._connected or self._engine is None:
            self._last_status = {"phase": "cancelled", "message": "navigation cancelled"}
            return self._last_status
        self._last_status = self._engine.cancel()
        return self._last_status

    def snapshot_runtime(self, robot_id: str, current_state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = dict(current_state or {})
        nav_state = dict(state.get("nav_state") or {})
        status = self._last_status or (
            {} if self._engine is None else self._engine.get_status()
        )
        latest_observation = self._latest_observation()
        if latest_observation is not None:
            pose_x, pose_y, pose_yaw = latest_observation.pose_xy_yaw
            state["robot_pose"] = {
                "frame": "map",
                "x": float(pose_x),
                "y": float(pose_y),
                "z": 0.0,
                "yaw": float(pose_yaw),
                "stamp": self._timestamp(),
            }
        state["nav_state"] = {
            "mode": self._phase_to_mode(status.get("phase")),
            "status": self._phase_to_status(status.get("phase")),
            "goal_id": status.get("target_label"),
            "target_ref": None if not status.get("target_label") else {
                "kind": "target_label",
                "id": status["target_label"],
                "label": status["target_label"],
            },
            "target_label": status.get("target_label"),
            "goal": self._goal_from_status(status),
            "path_progress": self._path_progress(status),
            "recovery_count": nav_state.get("recovery_count", 0),
            "last_error": self._last_error(status),
            "relocalization_confidence": nav_state.get("relocalization_confidence"),
            "active_horizon_target": status.get("active_horizon_target"),
            "history_tail": status.get("history_tail"),
        }
        return {robot_id: state}

    def _sdk_api(self) -> dict[str, Any]:
        if self._sdk is None:
            self._sdk = _import_navigation_sdk()
        return self._sdk

    def _bridge_config_kwargs(self) -> dict[str, Any]:
        allowed = {
            "host_bind",
            "video_port",
            "state_port",
            "occupancy_port",
            "depth_port",
            "motion_port",
            "ssh_host",
            "ssh_user",
            "ssh_password",
            "ssh_options",
            "remote_project_dir",
            "remote_python",
            "remote_setup",
            "remote_ros_choice",
            "remote_sudo_password",
            "auto_start_remote",
            "remote_livox_setup",
            "remote_livox_launch",
            "remote_data_script",
            "remote_motion_script",
            "remote_data_command",
            "remote_motion_command",
            "remote_data_video_backend",
            "remote_data_video_index",
            "remote_motion_backend",
            "remote_motion_sdk_python_path",
            "remote_motion_network_interface",
            "remote_motion_require_subscriber",
            "remote_sync_before_start",
            "remote_sync_paths",
            "remote_sync_excludes",
            "remote_startup_delay_s",
            "remote_observation_wait_timeout_s",
            "forward_speed_x",
            "turn_speed_z",
            "motion_confirm_timeout_s",
            "motion_confirm_translation_m",
            "motion_confirm_rotation_deg",
            "horizon_confirm_timeout_s",
            "horizon_confirm_translation_m",
            "horizon_confirm_rotation_deg",
            "lateral_speed_y",
        }
        return {key: value for key, value in self.config.items() if key in allowed}

    @staticmethod
    def _timestamp() -> str:
        from datetime import datetime

        return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    def _latest_observation(self) -> Any:
        if self._bridge is None:
            return None
        get_observation = getattr(self._bridge, "get_observation", None)
        if not callable(get_observation):
            return None
        try:
            return get_observation()
        except Exception:
            return None

    @staticmethod
    def _phase_to_status(phase: str | None) -> str:
        mapping = {
            "idle": "idle",
            "searching": "navigating",
            "tracking": "navigating",
            "success": "arrived",
            "blocked": "blocked",
            "not_found": "failed",
            "cancelled": "stopped",
        }
        return mapping.get(str(phase or "idle"), "idle")

    @staticmethod
    def _phase_to_mode(phase: str | None) -> str:
        mapping = {
            "idle": "idle",
            "searching": "navigating",
            "tracking": "navigating",
            "success": "navigating",
            "blocked": "navigating",
            "not_found": "navigating",
            "cancelled": "idle",
        }
        return mapping.get(str(phase or "idle"), "idle")

    def _goal_from_status(self, status: dict[str, Any]) -> dict[str, Any] | None:
        observation = self._latest_observation()
        horizon = status.get("active_horizon_target") or {}
        if observation is None and not horizon:
            return None
        pose = (0.0, 0.0, 0.0) if observation is None else observation.pose_xy_yaw
        return {
            "x": float(pose[0]),
            "y": float(pose[1]),
            "yaw": float(pose[2]),
            "horizon": horizon or None,
        }

    @staticmethod
    def _path_progress(status: dict[str, Any]) -> float | None:
        phase = status.get("phase")
        if phase == "success":
            return 1.0
        if phase in {"searching", "tracking"}:
            return min(0.99, max(0.0, float(status.get("steps", 0)) / 10.0))
        if phase == "blocked":
            return 0.5
        return None

    @staticmethod
    def _last_error(status: dict[str, Any]) -> str | None:
        phase = status.get("phase")
        if phase == "blocked":
            return "recoverable_obstacle"
        if phase == "not_found":
            return "target_not_found"
        return None


def normalize_status_payload(payload: Any) -> dict[str, Any]:
    """Convert SDK dataclasses into plain dicts when needed."""
    if isinstance(payload, dict):
        return payload
    if is_dataclass(payload):
        return asdict(payload)
    raise TypeError(f"Unsupported payload type: {type(payload)!r}")
