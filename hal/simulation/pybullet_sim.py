"""
hal/simulation/pybullet_sim.py

PyBullet-backed physics simulator for Physical Agent Operating System.

Responsibilities
────────────────
- Spawn a PyBullet world (GUI or DIRECT mode).
- Load objects described in the scene dict (from ENVIRONMENT.md).
- Execute high-level robot actions (move_to, pick_up, put_down, push, …).
- Return the post-execution scene state so ENVIRONMENT.md can be updated.

PyBullet is an *optional* dependency.  If it is not installed, the module
raises a clear ImportError with install instructions.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

try:
    import pybullet as pb
    import pybullet_data
except ImportError as exc:
    raise ImportError(
        "PyBullet is required for physics simulation.\n"
        "Install it with:  pip install pybullet"
    ) from exc

# Object half-extents (metres) used when spawning simple box colliders
_OBJECT_HALF_EXTENTS: dict[str, tuple[float, float, float]] = {
    "fruit":     (0.03, 0.03, 0.03),
    "container": (0.04, 0.04, 0.06),
    "default":   (0.025, 0.025, 0.025),
}

# Table surface height (metres)
_TABLE_Z = 0.50
# Height of the plane (ground)
_GROUND_Z = 0.0

# Common colour look-up so objects match their semantic colour attribute
_COLOR_MAP: dict[str, list[float]] = {
    "red":    [0.85, 0.20, 0.20, 1.0],
    "blue":   [0.20, 0.40, 0.90, 1.0],
    "green":  [0.20, 0.80, 0.20, 1.0],
    "yellow": [0.90, 0.85, 0.20, 1.0],
    "orange": [0.95, 0.50, 0.10, 1.0],
    "white":  [0.95, 0.95, 0.95, 1.0],
    "black":  [0.10, 0.10, 0.10, 1.0],
    "gray":   [0.50, 0.50, 0.50, 1.0],
    "grey":   [0.50, 0.50, 0.50, 1.0],
    "purple": [0.60, 0.20, 0.80, 1.0],
    "pink":   [0.95, 0.50, 0.70, 1.0],
    "brown":  [0.50, 0.30, 0.10, 1.0],
}


class PyBulletSimulator:
    """Thin wrapper around PyBullet for PhyAgentOS simulation.

    Parameters
    ----------
    gui:
        If *True* open a 3-D viewer window; otherwise run headlessly
        (DIRECT mode).  Use ``gui=True`` for manual inspection and
        ``gui=False`` (default) for automated tests.
    gravity:
        Gravitational acceleration in m/s² (default 9.81).
    """

    def __init__(self, gui: bool = False, gravity: float = 9.81) -> None:
        self._gui = gui
        self._client = pb.connect(pb.GUI if gui else pb.DIRECT)
        pb.setAdditionalSearchPath(pybullet_data.getDataPath(),
                                   physicsClientId=self._client)
        pb.setGravity(0, 0, -gravity, physicsClientId=self._client)

        # Load ground plane and a simple table
        self._plane_id = pb.loadURDF(
            "plane.urdf", physicsClientId=self._client
        )
        self._table_id = self._spawn_table()

        # Maps object name → PyBullet body ID
        self._body_ids: dict[str, int] = {}

        # Robot "end-effector" position (simplified as a floating point)
        self._ee_pos: list[float] = [0.0, 0.0, _TABLE_Z + 0.08]

        # Whether the robot is currently holding an object
        self._held_object: str | None = None

        # Store original scene properties so get_scene() can preserve metadata
        self._scene_props: dict[str, dict] = {}

        # Visual marker for the end-effector so users can see the "robot"
        self._ee_body: int | None = None
        self._spawn_ee_marker()

        # Set camera to show the table nicely in GUI mode
        if gui:
            pb.resetDebugVisualizerCamera(
                cameraDistance=1.0,
                cameraYaw=45,
                cameraPitch=-30,
                cameraTargetPosition=[0, 0, _TABLE_Z],
                physicsClientId=self._client,
            )

    # ── Scene management ────────────────────────────────────────────────────

    def load_scene(self, scene: dict[str, dict]) -> None:
        """Spawn all objects described in *scene* into the PyBullet world.

        Existing objects are removed first so the scene can be reloaded.
        """
        self._clear_objects()
        self._scene_props.clear()
        for name, props in scene.items():
            self._scene_props[name] = dict(props)  # preserve original metadata
            pos = props.get("position", {})
            x = float(pos.get("x", 0)) / 100.0   # cm → m
            y = float(pos.get("y", 0)) / 100.0
            obj_type = props.get("type", "default")
            color = props.get("color", "")
            half = _OBJECT_HALF_EXTENTS.get(obj_type, _OBJECT_HALF_EXTENTS["default"])
            # Place objects ON TOP of the table surface (table_top = _TABLE_Z + 0.01)
            # + vertical half-extent so the bottom of the object rests on the surface
            if obj_type == "fruit":
                z_offset = max(half)  # sphere radius
            elif obj_type == "container":
                z_offset = half[2]  # half-height of cylinder
            else:
                z_offset = half[2]  # half-height of box
            z_user = float(pos.get("z", 0)) / 100.0
            # Auto-heal: reset z to 0 if object was "held" (from a previous
            # session) or has a negative/excessive z (corrupted data)
            obj_location = props.get("location", "table")
            if z_user < 0 or obj_location == "held" or z_user > 0.30:
                z_user = 0.0
            z = _TABLE_Z + 0.01 + z_offset + z_user
            body_id = self._spawn_object(name, (x, y, z), half, color=color, obj_type=obj_type)
            self._body_ids[name] = body_id

        # Run physics for a bit so objects settle onto the table under gravity
        self._step(steps=120)

    def get_scene(self) -> dict[str, dict]:
        """Return current object positions / states as a plain dict.

        This is written back to ENVIRONMENT.md after each action.
        """
        scene: dict[str, dict] = {}
        for name, body_id in self._body_ids.items():
            pos, _ = pb.getBasePositionAndOrientation(
                body_id, physicsClientId=self._client
            )
            # Convert back to centimetres, subtracting the FULL spawn offset
            # (table surface + table thickness + object half-extent)
            orig = self._scene_props.get(name, {})
            orig_type = orig.get("type", "default")
            orig_half = _OBJECT_HALF_EXTENTS.get(orig_type, _OBJECT_HALF_EXTENTS["default"])
            if orig_type == "fruit":
                z_spawn_offset = max(orig_half)
            elif orig_type == "container":
                z_spawn_offset = orig_half[2]
            else:
                z_spawn_offset = orig_half[2]
            x_cm = round(pos[0] * 100, 1)
            y_cm = round(pos[1] * 100, 1)
            z_cm = round((pos[2] - _TABLE_Z - 0.01 - z_spawn_offset) * 100, 1)

            location = "held" if name == self._held_object else (
                "table" if pos[2] >= _TABLE_Z - 0.05 else "floor"
            )
            entry: dict[str, object] = {
                "position": {"x": x_cm, "y": y_cm, "z": z_cm},
                "location": location,
            }
            # Preserve original metadata (color, type, etc.) so it survives save/reload
            orig = self._scene_props.get(name, {})
            if "type" in orig:
                entry["type"] = orig["type"]
            if "color" in orig:
                entry["color"] = orig["color"]
            scene[name] = entry
        return scene

    # ── Actions ─────────────────────────────────────────────────────────────

    def execute_action(self, action_type: str, params: dict) -> str:
        """Dispatch a high-level action and step the simulation.

        Returns a human-readable result string.
        """
        handlers = {
            "move_to":    self._move_to,
            "pick_up":    self._pick_up,
            "put_down":   self._put_down,
            "push":       self._push,
            "point_to":   self._point_to,
            "nod_head":   self._nod_head,
            "shake_head": self._shake_head,
        }
        handler = handlers.get(action_type)
        if handler is None:
            return f"Unknown action type: {action_type!r}"
        return handler(params)

    # ── Low-level action implementations ────────────────────────────────────

    def _move_to(self, params: dict) -> str:
        x = float(params.get("x", 0)) / 100.0
        y = float(params.get("y", 0)) / 100.0
        z = float(params.get("z", 0)) / 100.0 + _TABLE_Z
        self._ee_pos = [x, y, z]
        self._update_ee_marker()
        self._step()
        return f"End-effector moved to ({x*100:.1f}, {y*100:.1f}, {z*100:.1f}) cm."

    def _pick_up(self, params: dict) -> str:
        target = params.get("target", "")
        if target not in self._body_ids:
            return f"Failed: object '{target}' not found in scene."
        if self._held_object is not None:
            return f"Failed: already holding '{self._held_object}'."
        body_id = self._body_ids[target]
        # Move EE to object
        pos, _ = pb.getBasePositionAndOrientation(
            body_id, physicsClientId=self._client
        )
        self._ee_pos = list(pos)
        self._update_ee_marker()
        # Lift it
        lift_pos = (pos[0], pos[1], pos[2] + 0.20)
        pb.resetBasePositionAndOrientation(
            body_id, lift_pos, (0, 0, 0, 1), physicsClientId=self._client
        )
        pb.changeDynamics(
            body_id, -1, mass=0, physicsClientId=self._client
        )  # make static so gravity doesn't drop it
        self._held_object = target
        self._step()
        return f"Picked up '{target}'."

    def _put_down(self, params: dict) -> str:
        target = params.get("target", "")
        location = params.get("location", "table")
        if self._held_object != target:
            return f"Failed: not holding '{target}'."
        body_id = self._body_ids[target]
        # Determine drop position — must match load_scene offset formula
        # so get_scene() ↔ load_scene() round-trip is stable
        orig_type = self._scene_props.get(target, {}).get("type", "default")
        orig_half = _OBJECT_HALF_EXTENTS.get(orig_type, _OBJECT_HALF_EXTENTS["default"])
        z_offset = max(orig_half) if orig_type == "fruit" else orig_half[2]
        if location == "floor":
            drop_z = _GROUND_Z + 0.01 + z_offset
        else:  # table
            drop_z = _TABLE_Z + 0.01 + z_offset
        drop_pos = (self._ee_pos[0], self._ee_pos[1], drop_z)
        pb.resetBasePositionAndOrientation(
            body_id, drop_pos, (0, 0, 0, 1), physicsClientId=self._client
        )
        pb.changeDynamics(
            body_id, -1, mass=0.1, physicsClientId=self._client
        )  # restore mass so gravity works
        self._held_object = None
        self._step(steps=120)  # let physics settle
        return f"Put down '{target}' at '{location}'."

    def _push(self, params: dict) -> str:
        target = params.get("target", "")
        direction = params.get("direction", "forward")
        if target not in self._body_ids:
            return f"Failed: object '{target}' not found in scene."
        body_id = self._body_ids[target]
        impulse_map = {
            "forward":  (0,  0.5, 0),
            "backward": (0, -0.5, 0),
            "left":     (-0.5, 0, 0),
            "right":    (0.5, 0, 0),
        }
        impulse = impulse_map.get(direction, (0, 0.5, 0))
        pb.applyExternalForce(
            body_id, -1, impulse, (0, 0, 0), pb.WORLD_FRAME,
            physicsClientId=self._client,
        )
        self._step(steps=240)
        return f"Pushed '{target}' {direction}."

    def _point_to(self, params: dict) -> str:
        target = params.get("target", "")
        if target in self._body_ids:
            pos, _ = pb.getBasePositionAndOrientation(
                self._body_ids[target], physicsClientId=self._client
            )
            self._ee_pos = list(pos)
        return f"Pointed to '{target}'."

    def _nod_head(self, _params: dict) -> str:
        if self._gui:
            time.sleep(0.3)
        return "Nodded head."

    def _shake_head(self, _params: dict) -> str:
        if self._gui:
            time.sleep(0.3)
        return "Shook head."

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _spawn_table(self) -> int:
        """Spawn a flat static box to act as a table surface."""
        col = pb.createCollisionShape(
            pb.GEOM_BOX, halfExtents=[0.30, 0.30, 0.01],
            physicsClientId=self._client,
        )
        vis = pb.createVisualShape(
            pb.GEOM_BOX, halfExtents=[0.30, 0.30, 0.01],
            rgbaColor=[0.6, 0.4, 0.2, 1.0],
            physicsClientId=self._client,
        )
        return pb.createMultiBody(
            baseMass=0,
            baseCollisionShapeIndex=col,
            baseVisualShapeIndex=vis,
            basePosition=[0, 0, _TABLE_Z],
            physicsClientId=self._client,
        )

    def _spawn_object(
        self,
        name: str,
        position: tuple[float, float, float],
        half_extents: tuple[float, float, float],
        color: str = "",
        obj_type: str = "default",
    ) -> int:
        """Spawn a coloured shape representing an object.

        Shape selection based on object type:
        - fruit → sphere (apples, oranges, etc.)
        - container → cylinder (cups, bowls, etc.)
        - default → box
        """
        # Resolve colour
        if color and color.lower() in _COLOR_MAP:
            r, g, b, _ = _COLOR_MAP[color.lower()]
        else:
            import hashlib
            h = int(hashlib.md5(name.encode()).hexdigest()[:6], 16)
            r = ((h >> 16) & 0xFF) / 255.0
            g = ((h >> 8)  & 0xFF) / 255.0
            b = (h & 0xFF) / 255.0

        rgba = [r, g, b, 1.0]

        # Choose geometry based on object type
        if obj_type == "fruit":
            radius = max(half_extents)  # use largest half-extent as radius
            col = pb.createCollisionShape(
                pb.GEOM_SPHERE, radius=radius,
                physicsClientId=self._client,
            )
            vis = pb.createVisualShape(
                pb.GEOM_SPHERE, radius=radius,
                rgbaColor=rgba,
                physicsClientId=self._client,
            )
        elif obj_type == "container":
            radius = max(half_extents[0], half_extents[1])
            height = half_extents[2] * 2
            col = pb.createCollisionShape(
                pb.GEOM_CYLINDER, radius=radius, height=height,
                physicsClientId=self._client,
            )
            vis = pb.createVisualShape(
                pb.GEOM_CYLINDER, radius=radius, length=height,
                rgbaColor=rgba,
                physicsClientId=self._client,
            )
        else:
            col = pb.createCollisionShape(
                pb.GEOM_BOX, halfExtents=list(half_extents),
                physicsClientId=self._client,
            )
            vis = pb.createVisualShape(
                pb.GEOM_BOX, halfExtents=list(half_extents),
                rgbaColor=rgba,
                physicsClientId=self._client,
            )

        return pb.createMultiBody(
            baseMass=0.1,
            baseCollisionShapeIndex=col,
            baseVisualShapeIndex=vis,
            basePosition=list(position),
            physicsClientId=self._client,
        )

    def _spawn_ee_marker(self) -> None:
        """Spawn a visible green sphere with label to represent the robot end-effector."""
        # Larger sphere (4cm radius) so it's clearly visible as the robot
        col = pb.createCollisionShape(
            pb.GEOM_SPHERE, radius=0.04, physicsClientId=self._client,
        )
        vis = pb.createVisualShape(
            pb.GEOM_SPHERE, radius=0.04,
            rgbaColor=[0.2, 0.9, 0.3, 0.8],
            physicsClientId=self._client,
        )
        self._ee_body = pb.createMultiBody(
            baseMass=0,
            baseCollisionShapeIndex=col,
            baseVisualShapeIndex=vis,
            basePosition=self._ee_pos,
            physicsClientId=self._client,
        )
        # Add text label so users know this is the robot
        if self._gui:
            pb.addUserDebugText(
                "Robot EE",
                textPosition=[0, 0, 0.06],  # slightly above the sphere
                textColorRGB=[0.1, 0.7, 0.2],
                textSize=1.2,
                parentObjectUniqueId=self._ee_body,
                physicsClientId=self._client,
            )

    def _update_ee_marker(self) -> None:
        """Move the end-effector marker to the current position."""
        if self._ee_body is not None:
            pb.resetBasePositionAndOrientation(
                self._ee_body, self._ee_pos, (0, 0, 0, 1),
                physicsClientId=self._client,
            )

    def get_runtime_state(self) -> dict:
        """Return simulated robot runtime state for ENVIRONMENT.md."""
        from datetime import datetime, timezone
        return {
            "robots": {
                "sim_arm": {
                    "connection_state": {
                        "status": "connected",
                        "transport": "local_simulation",
                        "last_heartbeat": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "last_error": None,
                        "reconnect_attempts": 0,
                    },
                    "robot_pose": {
                        "frame": "world",
                        "x": round(self._ee_pos[0] * 100, 1),
                        "y": round(self._ee_pos[1] * 100, 1),
                        "z": round((self._ee_pos[2] - _TABLE_Z) * 100, 1),
                        "yaw": 0.0,
                    },
                    "nav_state": {
                        "mode": "holding" if self._held_object else "idle",
                        "status": "active",
                        "held_object": self._held_object,
                    },
                },
            },
        }

    def _clear_objects(self) -> None:
        """Remove all previously spawned objects from the world."""
        for body_id in self._body_ids.values():
            pb.removeBody(body_id, physicsClientId=self._client)
        self._body_ids.clear()
        self._held_object = None

    def _step(self, steps: int = 60) -> None:
        """Advance the simulation by *steps* timesteps."""
        for _ in range(steps):
            pb.stepSimulation(physicsClientId=self._client)
            if self._gui:
                time.sleep(1.0 / 240.0)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Disconnect from PyBullet and free resources."""
        try:
            pb.disconnect(physicsClientId=self._client)
        except pb.error:
            pass

    def __enter__(self) -> "PyBulletSimulator":
        return self

    def __exit__(self, *_) -> None:
        self.close()
