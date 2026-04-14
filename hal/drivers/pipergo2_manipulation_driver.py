"""
PiperGo2 manipulation HAL driver.

Bridges ACTION.md to PiperGo2ManipulationAPI (external sim stack).
Supports named waypoints, table-scene narration (from config), and pick/place presets.
"""

from __future__ import annotations

import importlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from hal.base_driver import BaseDriver

_PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


class PiperGo2ManipulationDriver(BaseDriver):
    """Bridge ACTION.md calls to PiperGo2ManipulationAPI methods."""

    def __init__(self, gui: bool = False, **kwargs: Any) -> None:
        self._gui = gui
        self._api = None
        self._env = None
        self._last_obs: Any = None
        self._last_scene: dict[str, dict] = {}

        self._scene_asset_path = str(kwargs.get("scene_asset_path", "")).strip()
        self._robot_start = tuple(kwargs.get("robot_start", (0.0, 0.0, 0.55)))
        self._arm_mass_scale = float(kwargs.get("arm_mass_scale", 1.0))
        self._objects_spec = kwargs.get("objects", [])
        self._api_kwargs = dict(kwargs.get("api_kwargs", {}))
        if gui:
            self._api_kwargs.setdefault("force_gui", True)

        self._waypoints: dict[str, list[float]] = self._normalize_waypoints(kwargs.get("waypoints", {}))
        self._waypoint_aliases: dict[str, str] = self._normalize_aliases(kwargs.get("waypoint_aliases", {}))
        self._navigation_action_name = kwargs.get("navigation_action_name")
        self._navigation_max_steps = int(kwargs.get("navigation_max_steps", 1200))
        self._navigation_threshold = float(kwargs.get("navigation_threshold", 0.10))

        self._visible_objects: list[dict[str, Any]] = list(kwargs.get("visible_objects", []))
        pp = kwargs.get("pick_place") or {}
        self._pick_target_raw = dict(pp.get("pick_target", {}))
        self._place_target_raw = dict(pp.get("place_target", {}))
        self._pick_place_output_dir = str(pp.get("output_dir", "/tmp/paos_pipergo2_logs"))
        self._pick_dump_name = str(pp.get("pick_dump", "room_pick.json"))
        self._place_dump_name = str(pp.get("place_dump", "room_place.json"))

        self._room_bootstrap = dict(kwargs.get("room_bootstrap", {}))
        self._pp_defaults = dict(kwargs.get("pick_place_defaults", {}))
        self._scene_narration_cn = ""
        self._last_pick_place_summary = ""
        self._pythonpath_entries = self._normalize_pythonpath(kwargs.get("pythonpath", []))
        self._ensure_pythonpath()
        self._idle_step_enabled = bool(kwargs.get("idle_step_enabled", True))
        self._idle_steps_per_cycle = int(kwargs.get("idle_steps_per_cycle", kwargs.get("idle_steps_per_poll", 1)))
        self._idle_step_interval_s = float(kwargs.get("idle_step_interval_s", 1.0 / 30.0))
        self._last_idle_step_ts = 0.0
        self._room_lighting = str(kwargs.get("room_lighting", "grey_studio")).strip().lower()
        self._camera_eye_offset = tuple(kwargs.get("camera_eye_offset", (-2.8, -2.2, 1.8)))
        self._camera_target_z_offset = float(kwargs.get("camera_target_z_offset", -0.4))
        self._camera_target_min_z = float(kwargs.get("camera_target_min_z", 0.2))
        self._eef_live_marker_enabled = bool(kwargs.get("eef_live_marker_enabled", False))

    @staticmethod
    def _normalize_pythonpath(raw: Any) -> list[str]:
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            p = str(item).strip()
            if not p:
                continue
            out.append(str(Path(p).expanduser().resolve()))
        return out

    def _ensure_pythonpath(self) -> None:
        for entry in reversed(self._pythonpath_entries):
            if entry in sys.path:
                continue
            sys.path.insert(0, entry)

    @staticmethod
    def _normalize_waypoints(raw: Any) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        if not isinstance(raw, dict):
            return out
        for k, v in raw.items():
            key = str(k).strip()
            if not key:
                continue
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                out[key] = [float(v[0]), float(v[1])]
        return out

    @staticmethod
    def _normalize_aliases(raw: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        if not isinstance(raw, dict):
            return out
        for k, v in raw.items():
            a = str(k).strip().lower()
            t = str(v).strip()
            if a and t:
                out[a] = t
        return out

    def get_profile_path(self) -> Path:
        return _PROFILES_DIR / "pipergo2_manipulation.md"

    def load_scene(self, scene: dict[str, dict]) -> None:
        self._last_scene = dict(scene or {})

    def execute_action(self, action_type: str, params: dict) -> str:
        handlers = {
            "start": self._start_from_action,
            "enter_simulation": self._start_from_action,
            "close": self._close_api,
            "step": self._step_env,
            "api_call": self._api_call,
            "navigate_to_waypoint": self._navigate_to_waypoint,
            "navigate_to_named": self._navigate_to_named,
            "describe_visible_scene": self._describe_visible_scene,
            "run_pick_place": self._run_pick_place,
        }
        handler = handlers.get(action_type)
        if handler is None:
            return f"Unknown action: {action_type}"
        try:
            return handler(params or {})
        except Exception as exc:  # pragma: no cover - runtime dependency bridge
            return f"Error: {type(exc).__name__}: {exc}"

    def get_scene(self) -> dict[str, dict]:
        scene = dict(self._last_scene)
        robot_xy = [float(self._robot_start[0]), float(self._robot_start[1])]
        navigable = sorted(set(self._waypoints.keys()) | set(self._waypoint_aliases.keys()))
        scene["manipulation_runtime"] = {
            "location": "sim",
            "state": "running" if self._api is not None else "idle",
            "robot_xy": robot_xy,
            "waypoint_keys": sorted(self._waypoints.keys()),
            "waypoint_aliases": dict(self._waypoint_aliases),
            "navigable_names": navigable,
            "gui_requested": bool(self._gui),
            "table_summary_cn": self._scene_narration_cn,
            "last_pick_place_cn": self._last_pick_place_summary,
            "last_obs": self._obs_brief(self._last_obs),
        }
        return scene

    def get_runtime_state(self) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        for vo in self._visible_objects:
            ok = vo.get("object_key")
            if not ok:
                continue
            nodes.append(
                {
                    "id": f"obj_{ok}",
                    "class": vo.get("shape_cn", "object"),
                    "object_key": ok,
                    "color_label_cn": vo.get("color_label_cn", ""),
                    "role": vo.get("role", ""),
                }
            )
        return {
            "scene_graph": {
                "nodes": nodes,
                "edges": [],
            }
        }

    def health_check(self) -> bool:
        """Keep simulator UI responsive even when ACTION.md is idle."""
        if self._idle_step_enabled:
            self._idle_step_if_due()
        return True

    def close(self) -> None:
        self._close_api({})

    def _resolve_nav_action_name(self) -> str:
        if self._navigation_action_name:
            return str(self._navigation_action_name)
        self._ensure_pythonpath()
        rob = importlib.import_module("internutopia_extension.configs.robots.pipergo2")
        return rob.move_to_point_cfg.name

    def _start_from_action(self, params: dict[str, Any]) -> str:
        if self._api is not None:
            return "Manipulation API already started."

        scene_asset_path = str(params.get("scene_asset_path", self._scene_asset_path)).strip()
        if not scene_asset_path:
            return "Error: missing scene_asset_path in driver config or action params."
        if not Path(scene_asset_path).exists():
            return f"Error: scene file not found: {scene_asset_path}"

        robot_start = params.get("robot_start", list(self._robot_start))
        arm_mass_scale = float(params.get("arm_mass_scale", self._arm_mass_scale))
        objects_spec = params.get("objects", self._objects_spec)
        api_kwargs = dict(self._api_kwargs)
        api_kwargs.update(params.get("api_kwargs", {}))

        self._api = self._build_api(
            scene_asset_path=scene_asset_path,
            robot_start=robot_start,
            arm_mass_scale=arm_mass_scale,
            objects_spec=objects_spec,
            api_kwargs=api_kwargs,
        )
        self._last_obs = self._api.start()
        marker_enabled = bool(params.get("eef_live_marker_enabled", self._eef_live_marker_enabled))
        if not marker_enabled:
            self._disable_api_eef_live_marker()
        self._env = getattr(self._api, "_env", None)
        if isinstance(robot_start, list) and len(robot_start) >= 3:
            self._robot_start = tuple(float(x) for x in robot_start[:3])

        rb = dict(self._room_bootstrap)
        rb.update(params.get("room_bootstrap") or {})
        if rb.get("enabled", True) and not params.get("skip_room_bootstrap"):
            boot_msg = self._room_bootstrap_sequence(rb)
            self._rebuild_scene_narration()
            return f"Manipulation API started. {boot_msg}"
        self._rebuild_scene_narration()
        return "Manipulation API started."

    def _disable_api_eef_live_marker(self) -> None:
        if self._api is None:
            return
        try:
            # Disable per-step debug sphere update in InternUtopia API.
            setattr(self._api, "_update_eef_debug_marker", lambda _obs: None)
        except Exception:
            pass
        try:
            import omni

            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return
            marker_prim = stage.GetPrimAtPath("/World/debug_eef_live_marker")
            if marker_prim and marker_prim.IsValid():
                stage.RemovePrim("/World/debug_eef_live_marker")
        except Exception:
            pass

    def _room_bootstrap_sequence(self, rb: dict[str, Any]) -> str:
        if self._api is None or self._env is None:
            return ""
        steps: list[str] = []
        if rb.get("apply_room_lighting", True):
            try:
                if self._apply_room_lighting():
                    steps.append("lighting:grey_studio")
            except Exception as exc:
                steps.append(f"lighting_skipped:{exc}")
        if rb.get("focus_view_on_robot", True):
            try:
                self._focus_view_xy(self._robot_start[:2], float(self._robot_start[2]))
                steps.append("viewport_focus")
            except Exception as exc:
                steps.append(f"viewport_focus_skipped:{exc}")
        if rb.get("collision_patch", True):
            try:
                self._collision_patch_merom_scene()
                steps.append("collision_patch")
            except Exception as exc:
                steps.append(f"collision_patch_skipped:{exc}")
        masses = rb.get("set_masses") or {}
        if isinstance(masses, dict):
            for name, mass in masses.items():
                try:
                    obj = self._api._env.runner.get_obj(str(name))
                    obj.set_mass(float(mass))
                    steps.append(f"mass:{name}")
                except Exception:
                    pass
        n_prev = int(rb.get("scene_preview_steps", 0))
        for _ in range(max(0, n_prev)):
            with self._env_lock:
                self._last_obs, _, _, _, _ = self._env.step({})
        if n_prev:
            steps.append(f"preview_steps:{n_prev}")
        n_stab = int(rb.get("stabilize_steps", 0))
        if n_stab > 0:
            xy = (float(self._robot_start[0]), float(self._robot_start[1]))
            self._stabilize_robot(xy, n_stab)
            steps.append(f"stabilize:{n_stab}")
        return "bootstrap[" + ",".join(steps) + "]"

    def _focus_view_xy(self, robot_xy: tuple[float, ...], robot_z: float) -> None:
        try:
            from isaacsim.core.utils.viewports import set_camera_view
        except ImportError:
            try:
                from omni.isaac.core.utils.viewports import set_camera_view
            except ImportError:
                return
        eye = [
            float(robot_xy[0]) + float(self._camera_eye_offset[0]),
            float(robot_xy[1]) + float(self._camera_eye_offset[1]),
            float(robot_z) + float(self._camera_eye_offset[2]),
        ]
        target = [
            float(robot_xy[0]),
            float(robot_xy[1]),
            max(float(robot_z) + float(self._camera_target_z_offset), float(self._camera_target_min_z)),
        ]
        set_camera_view(
            eye=eye,
            target=target,
            camera_prim_path="/OmniverseKit_Persp",
        )

    def _apply_room_lighting(self) -> bool:
        """Best-effort switch to Grey Studio style environment lighting."""
        if self._room_lighting not in {"grey_studio", "gray_studio"}:
            return False
        # Avoid omni.kit.commands here because some Isaac builds log hard errors
        # when these commands are unregistered (even if wrapped in try/except).
        try:
            from omni.kit.app import get_app  # type: ignore
            settings = get_app().get_settings()
            for key, value in (
                ("/rtx/environment/visible", True),
                ("/rtx/environment/mode", "Studio"),
                ("/rtx/environment/lightRig", "Grey Studio"),
                ("/rtx/environment/lightRigName", "Grey Studio"),
                ("/rtx/sceneDb/ambientLightIntensity", 0.35),
            ):
                try:
                    settings.set(key, value)
                except Exception:
                    pass
            # Optional: also try setting a named dome light color when present.
            try:
                from pxr import Sdf  # type: ignore
                stage = self._api._env.runner._world.stage if self._api and self._env else None
                if stage is not None:
                    for path in ("/Environment/DomeLight", "/World/DomeLight"):
                        prim = stage.GetPrimAtPath(path)
                        if prim and prim.IsValid():
                            attr = prim.GetAttribute("inputs:color")
                            if attr:
                                attr.Set((0.72, 0.74, 0.78))
                            break
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _collision_patch_merom_scene(self) -> None:
        from pxr import PhysxSchema, Usd, UsdPhysics

        stage = self._api._env.runner._world.stage
        scene_root = stage.GetPrimAtPath("/World/env_0/scene")
        if not scene_root.IsValid():
            return
        for prim in Usd.PrimRange(scene_root):
            if prim.IsInstance():
                prim.SetInstanceable(False)
        for prim in Usd.PrimRange(scene_root):
            if prim.GetTypeName() != "Mesh":
                continue
            try:
                UsdPhysics.CollisionAPI.Apply(prim)
                physx = PhysxSchema.PhysxCollisionAPI.Apply(prim)
                physx.CreateApproximationAttr().Set("convexHull")
            except Exception:
                pass

    def _stabilize_robot(self, target_xy: tuple[float, float], settle_steps: int) -> None:
        action_name = self._resolve_nav_action_name()
        idle_action = {action_name: [(float(target_xy[0]), float(target_xy[1]), 0.0)]}
        for _ in range(settle_steps):
            self._last_obs, _, terminated, _, _ = self._env.step(action=idle_action)
            ep = terminated[0] if isinstance(terminated, (list, tuple)) else bool(terminated)
            if ep:
                break

    def _close_api(self, _params: dict[str, Any]) -> str:
        if self._api is None:
            return "Manipulation API already closed."
        try:
            self._api.close()
        finally:
            self._api = None
            self._env = None
        return "Manipulation API closed."

    def _step_env(self, params: dict[str, Any]) -> str:
        if self._env is None:
            return "Error: API not started. Dispatch action_type='start' first."
        action = params.get("action", {})
        self._last_obs, _, _, _, _ = self._env.step(action=action)
        return "Environment stepped."

    def _idle_step_if_due(self) -> None:
        if self._api is None or self._env is None:
            return
        now = time.monotonic()
        if now - self._last_idle_step_ts < self._idle_step_interval_s:
            return
        self._last_idle_step_ts = now
        steps = max(1, self._idle_steps_per_cycle)
        for _ in range(steps):
            self._last_obs, _, _, _, _ = self._env.step(action={})

    def _api_call(self, params: dict[str, Any]) -> str:
        if self._api is None:
            return "Error: API not started. Dispatch action_type='start' first."
        method_name = str(params.get("method", "")).strip()
        if not method_name:
            return "Error: parameters.method is required for api_call."
        method = getattr(self._api, method_name, None)
        if method is None or not callable(method):
            return f"Error: method not found on API: {method_name}"
        args = params.get("args", [])
        kwargs = params.get("kwargs", {})
        if not isinstance(args, list):
            return "Error: parameters.args must be a list."
        if not isinstance(kwargs, dict):
            return "Error: parameters.kwargs must be an object."
        result = method(*args, **kwargs)
        return f"api_call ok: {method_name} => {self._safe_json(result)}"

    def _resolve_waypoint_key(self, key: str) -> str:
        k = key.strip()
        for canon in self._waypoints:
            if canon.lower() == k.lower():
                return canon
        alias = self._waypoint_aliases.get(k.lower())
        if alias:
            at = alias.strip()
            for canon in self._waypoints:
                if canon.lower() == at.lower():
                    return canon
        return k

    def _navigate_to_named(self, params: dict[str, Any]) -> str:
        raw_key = str(params.get("waypoint_key") or params.get("target") or "").strip()
        if not raw_key:
            return "Error: waypoint_key or target is required (e.g. staging_table or desk)."
        key = self._resolve_waypoint_key(raw_key)
        wp = self._waypoints.get(key)
        if not wp:
            known = sorted(self._waypoints.keys())
            als = sorted(self._waypoint_aliases.keys())
            return (
                f"Error: unknown waypoint_key={raw_key!r} (resolved={key!r}). "
                f"Known waypoints: {known}. Aliases: {als}"
            )
        return self._navigate_xy(
            [float(wp[0]), float(wp[1])],
            max_steps=int(params.get("max_steps", self._navigation_max_steps)),
            threshold=float(params.get("threshold", self._navigation_threshold)),
        )

    def _navigate_to_waypoint(self, params: dict[str, Any]) -> str:
        waypoint = params.get("waypoint_xy")
        if not (isinstance(waypoint, list) and len(waypoint) >= 2):
            return "Error: waypoint_xy must be [x, y]."
        return self._navigate_xy(
            [float(waypoint[0]), float(waypoint[1])],
            max_steps=int(params.get("max_steps", self._navigation_max_steps)),
            threshold=float(params.get("threshold", self._navigation_threshold)),
            action_name_override=(str(params["action_name"]) if params.get("action_name") else None),
        )

    def _navigate_xy(
        self,
        xy: list[float],
        *,
        max_steps: int,
        threshold: float,
        action_name_override: str | None = None,
    ) -> str:
        if self._env is None:
            return "Error: API not started. Dispatch action_type='start' first."
        action_name = str(action_name_override or self._resolve_nav_action_name())
        goal_action = {action_name: [(float(xy[0]), float(xy[1]), 0.0)]}
        dist = 9999.0
        for _ in range(max_steps):
            self._last_obs, _, terminated, _, _ = self._env.step(action=goal_action)
            robot_obs = self._extract_robot_obs(self._last_obs)
            if not robot_obs:
                continue
            pos = robot_obs.get("position")
            if not isinstance(pos, (list, tuple)) or len(pos) < 2:
                continue
            dx = float(pos[0]) - float(xy[0])
            dy = float(pos[1]) - float(xy[1])
            dist = math.hypot(dx, dy)
            episode_terminated = terminated[0] if isinstance(terminated, (list, tuple)) else bool(terminated)
            if episode_terminated:
                return "navigate terminated early."
            if dist <= threshold:
                return f"navigate ok: reached xy=({xy[0]:.4f},{xy[1]:.4f}), dist={dist:.4f}"
        return f"navigate failed: dist={dist:.4f}"

    def _rebuild_scene_narration(self) -> None:
        self._scene_narration_cn = self._build_table_narration_cn()

    def _build_table_narration_cn(self) -> str:
        if not self._visible_objects:
            return "No visible_objects configured; please define them in driver-config."
        cubes = [vo for vo in self._visible_objects if "cube" in str(vo.get("shape_cn", "")).lower()]
        others = [vo for vo in self._visible_objects if vo not in cubes]
        parts: list[str] = []
        if cubes:
            colors = [str(vo.get("color_label_cn", "")).strip() for vo in cubes if vo.get("color_label_cn")]
            uq = []
            for c in colors:
                if c and c not in uq:
                    uq.append(c)
            if uq:
                parts.append(f"multiple cubes with colors including {', '.join(uq)}")
            else:
                parts.append(f"{len(cubes)} cubes on the table")
        for vo in others:
            shape = vo.get("shape_cn", "object")
            col = vo.get("color_label_cn", "")
            parts.append(f"{col} {shape}".strip() if col else f"{shape}")
        return "I can see: " + "; ".join(parts) + "."

    def _describe_visible_scene(self, _params: dict[str, Any]) -> str:
        self._rebuild_scene_narration()
        return f"scene_description: {self._scene_narration_cn}"

    def _run_pick_place(self, params: dict[str, Any]) -> str:
        if self._api is None:
            return "Error: API not started. Dispatch action_type='start' first."
        defaults = dict(self._pp_defaults)
        execute_place = bool(params.get("execute_place", defaults.get("default_execute_place", True)))
        return_home = bool(params.get("return_home_after_place", defaults.get("return_home_after_place", False)))
        navigate_after_pick = bool(
            params.get("navigate_after_pick", defaults.get("navigate_to_place_pedestal_after_pick", False))
        )

        hint = self._normalize_color_hint(params.get("target_color_cn", "") or params.get("color_hint", ""))
        primary = str(defaults.get("primary_pick_object_key", "pick_cube"))
        keywords = [self._normalize_color_hint(x) for x in defaults.get("primary_pick_color_keywords", ["red"])]
        if hint:
            matched = any(k in hint for k in keywords)
            if not matched:
                return f"Error: pick hint {hint!r} does not match configured primary pick keywords {keywords}."

        pick_target = self._tupleize_grasp_dict(self._pick_target_raw)
        place_target = self._tupleize_grasp_dict(self._place_target_raw)
        if not pick_target or not pick_target.get("position"):
            return "Error: pick_place.pick_target missing in driver-config."

        out_dir = Path(params.get("output_dir", self._pick_place_output_dir))
        out_dir.mkdir(parents=True, exist_ok=True)
        pick_name = str(params.get("pick_dump", self._pick_dump_name))
        place_name = str(params.get("place_dump", self._place_dump_name))
        pick_path = out_dir / pick_name

        pick_result = self._api.pick(pick_target, dump_path=pick_path)
        pick_ok = self._result_ok(pick_result)
        lines = [f"pick success={pick_ok} steps={self._result_steps(pick_result)}"]
        if execute_place and pick_ok:
            if not place_target or not place_target.get("position"):
                return "Error: execute_place true but place_target missing in driver-config."
            place_path = out_dir / place_name
            place_result = self._api.release(place_target, dump_path=place_path)
            lines.append(
                f"place success={self._result_ok(place_result)} steps={self._result_steps(place_result)}"
            )
        elif navigate_after_pick and pick_ok:
            nav_xy_raw = params.get("navigate_after_pick_xy")
            if isinstance(nav_xy_raw, (list, tuple)) and len(nav_xy_raw) >= 2:
                nav_xy = [float(nav_xy_raw[0]), float(nav_xy_raw[1])]
            elif place_target and place_target.get("position") and len(place_target["position"]) >= 2:
                nav_xy = [float(place_target["position"][0]), float(place_target["position"][1])]
            else:
                nav_xy = None
            if nav_xy:
                lines.append(
                    self._navigate_xy(
                        nav_xy,
                        max_steps=self._navigation_max_steps,
                        threshold=self._navigation_threshold,
                    )
                )
            else:
                lines.append("navigate_after_pick skipped (no valid target xy).")
        elif not execute_place:
            lines.append("place skipped (execute_place=false).")

        if return_home and self._waypoints.get("robot_home"):
            home = self._waypoints["robot_home"]
            lines.append(
                self._navigate_xy(
                    [float(home[0]), float(home[1])],
                    max_steps=self._navigation_max_steps,
                    threshold=self._navigation_threshold,
                )
            )

        self._last_pick_place_summary = "；".join(lines)
        return self._last_pick_place_summary

    @staticmethod
    def _normalize_color_hint(raw: Any) -> str:
        hint = str(raw or "").strip().lower()
        if not hint:
            return ""
        alias_map = {
            "红": "red",
            "紅": "red",
            "红色": "red",
            "紅色": "red",
            "蓝": "blue",
            "藍": "blue",
            "蓝色": "blue",
            "藍色": "blue",
            "绿": "green",
            "綠": "green",
            "绿色": "green",
            "綠色": "green",
            "黄": "yellow",
            "黃": "yellow",
            "黄色": "yellow",
            "黃色": "yellow",
        }
        return alias_map.get(hint, hint)

    @staticmethod
    def _result_ok(result: Any) -> bool:
        if result is None:
            return False
        if hasattr(result, "success"):
            return bool(getattr(result, "success"))
        if isinstance(result, dict):
            return bool(result.get("success", False))
        return True

    @staticmethod
    def _result_steps(result: Any) -> Any:
        if hasattr(result, "steps"):
            return getattr(result, "steps")
        if isinstance(result, dict):
            return result.get("steps", "?")
        return "?"

    @staticmethod
    def _tupleize_grasp_dict(raw: dict[str, Any]) -> dict[str, Any]:
        if not raw:
            return {}
        out: dict[str, Any] = {}
        for k, v in raw.items():
            if k in ("position", "pre_position", "post_position", "orientation") and isinstance(v, list):
                out[k] = tuple(float(x) for x in v)
            elif k == "metadata" and isinstance(v, dict):
                out[k] = dict(v)
            else:
                out[k] = v
        return out

    @staticmethod
    def _build_api(
        *,
        scene_asset_path: str,
        robot_start: Any,
        arm_mass_scale: float,
        objects_spec: Any,
        api_kwargs: dict[str, Any],
    ):
        pythonpath = api_kwargs.pop("pythonpath", None)
        if pythonpath:
            if isinstance(pythonpath, str):
                pythonpath = [pythonpath]
            for entry in reversed(pythonpath):
                ep = str(Path(str(entry)).expanduser().resolve())
                if ep not in sys.path:
                    sys.path.insert(0, ep)
        bridge = importlib.import_module("internutopia.bridge")
        objects_mod = importlib.import_module("internutopia_extension.configs.objects")
        PiperGo2ManipulationAPI = bridge.PiperGo2ManipulationAPI
        create_pipergo2_robot_cfg = bridge.create_pipergo2_robot_cfg
        DynamicCubeCfg = objects_mod.DynamicCubeCfg
        VisualCubeCfg = objects_mod.VisualCubeCfg

        rs = robot_start
        if isinstance(rs, list):
            rs = tuple(float(x) for x in rs)
        else:
            rs = tuple(rs)
        robot_cfg = create_pipergo2_robot_cfg(position=rs, arm_mass_scale=arm_mass_scale)
        objects = []
        if isinstance(objects_spec, list):
            for item in objects_spec:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind", "dynamic_cube")).strip().lower()
                cfg = {
                    "name": item["name"],
                    "prim_path": item["prim_path"],
                    "position": tuple(float(x) for x in item["position"]),
                    "scale": tuple(float(x) for x in item["scale"]),
                    "color": item.get("color", (0.5, 0.5, 0.5)),
                }
                if kind == "visual_cube":
                    cfg["color"] = list(cfg["color"])
                    objects.append(VisualCubeCfg(**cfg))
                else:
                    objects.append(DynamicCubeCfg(**cfg))

        headless = api_kwargs.pop("headless", None)
        if headless is None:
            headless = not bool(api_kwargs.pop("force_gui", False))
        return PiperGo2ManipulationAPI(
            scene_asset_path=scene_asset_path,
            robot_cfg=robot_cfg,
            objects=objects,
            headless=headless,
            **api_kwargs,
        )

    @staticmethod
    def _safe_obs(value: Any) -> Any:
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except TypeError:
            return str(value)

    @staticmethod
    def _safe_json(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return str(value)

    @staticmethod
    def _obs_brief(obs_data: Any) -> dict[str, Any] | None:
        """Return a tiny snapshot to avoid bloating ENVIRONMENT.md."""
        robot = PiperGo2ManipulationDriver._extract_robot_obs(obs_data)
        if not isinstance(robot, dict):
            return None
        pos = robot.get("position")
        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
            return {
                "position": [float(pos[0]), float(pos[1]), float(pos[2])],
                "render": bool(robot.get("render", False)),
            }
        return {
            "render": bool(robot.get("render", False)),
        }

    @staticmethod
    def _extract_robot_obs(obs_data: Any) -> dict[str, Any] | None:
        if isinstance(obs_data, dict) and "position" in obs_data:
            return obs_data
        if isinstance(obs_data, dict) and "pipergo2" in obs_data:
            return obs_data["pipergo2"]
        if isinstance(obs_data, dict) and "pipergo2_0" in obs_data:
            return obs_data["pipergo2_0"]
        if isinstance(obs_data, (list, tuple)) and len(obs_data) > 0:
            first = obs_data[0]
            if isinstance(first, dict) and "pipergo2" in first:
                return first["pipergo2"]
            if isinstance(first, dict) and "pipergo2_0" in first:
                return first["pipergo2_0"]
            if isinstance(first, dict):
                return first
        return None
