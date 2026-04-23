"""
hal/drivers/simulation_driver.py

SimulationDriver — reference BaseDriver implementation backed by PyBullet.

This is the first (and reference) driver.  Other driver authors should
follow the same pattern: subclass BaseDriver, implement the four abstract
methods, and place the file under ``hal/drivers/``.
"""

from __future__ import annotations

from pathlib import Path

from hal.base_driver import BaseDriver


# Locate the profiles directory relative to this file
_PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


class SimulationDriver(BaseDriver):
    """PyBullet-backed physics simulation driver.

    Parameters
    ----------
    gui:
        Open a 3-D viewer window (default ``False`` for headless CI).
    """

    def __init__(self, gui: bool = False, **_kwargs) -> None:
        # Lazy import so pybullet is only required when this driver is used
        from hal.simulation.pybullet_sim import PyBulletSimulator

        self._gui = gui
        self._sim = PyBulletSimulator(gui=gui)

    # ── BaseDriver interface ────────────────────────────────────────────

    def get_profile_path(self) -> Path:
        return _PROFILES_DIR / "simulation.md"

    def load_scene(self, scene: dict[str, dict]) -> None:
        self._sim.load_scene(scene)

    def execute_action(self, action_type: str, params: dict) -> str:
        return self._sim.execute_action(action_type, params)

    def get_scene(self) -> dict[str, dict]:
        return self._sim.get_scene()

    def get_runtime_state(self) -> dict:
        """Return simulated robot runtime state."""
        return self._sim.get_runtime_state()

    def close(self) -> None:
        self._sim.close()
