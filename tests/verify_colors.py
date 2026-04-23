"""Verify: shapes, colors, z-stability across reload, and robot actions."""
import pybullet as pb
from hal.simulation.pybullet_sim import PyBulletSimulator, _COLOR_MAP

scene = {
    "red_apple": {"type": "fruit", "color": "red", "position": {"x": 5, "y": 5, "z": 0}},
    "blue_cup": {"type": "container", "color": "blue", "position": {"x": -10, "y": 3, "z": 0}},
}

SHAPE_NAMES = {pb.GEOM_BOX: "BOX", pb.GEOM_SPHERE: "SPHERE", pb.GEOM_CYLINDER: "CYLINDER"}
EXP_SHAPE = {"fruit": pb.GEOM_SPHERE, "container": pb.GEOM_CYLINDER}

sim = PyBulletSimulator(gui=False)
ok = True

# === Test 1: Shape & Color ===
print("=== Shape & Color ===")
sim.load_scene(scene)
for nm, bid in sim._body_ids.items():
    v = pb.getVisualShapeData(bid, physicsClientId=sim._client)
    shape = SHAPE_NAMES.get(v[0][2], "?")
    rgba = [round(c,2) for c in v[0][7]]
    exp_s = SHAPE_NAMES.get(EXP_SHAPE.get(scene[nm]["type"], pb.GEOM_BOX))
    exp_c = [round(c,2) for c in _COLOR_MAP.get(scene[nm]["color"], [])]
    s_ok = shape == exp_s
    c_ok = rgba[:3] == exp_c[:3]
    if not s_ok or not c_ok: ok = False
    print(f"  {nm}: shape={shape}({'OK' if s_ok else 'FAIL'}) color={rgba[:3]}({'OK' if c_ok else 'FAIL'})")

# === Test 2: Z-stability across 3 reload cycles ===
print("\n=== Z-Stability (3 reload cycles) ===")
for cycle in range(3):
    saved = sim.get_scene()
    z_vals = {n: saved[n]["position"]["z"] for n in saved}
    sim.load_scene(saved)
    print(f"  Cycle {cycle+1}: apple_z={z_vals['red_apple']}, cup_z={z_vals['blue_cup']}")
    if cycle > 0:
        for n in z_vals:
            if abs(z_vals[n]) > 1.0:  # should stay near 0
                print(f"  DRIFT DETECTED: {n} z={z_vals[n]} [FAIL]")
                ok = False

# === Test 3: Robot actions ===
print("\n=== Robot Actions ===")
result = sim.execute_action("pick_up", {"target": "red_apple"})
print(f"  pick_up: {result}")
if "Picked up" not in result: ok = False

result = sim.execute_action("put_down", {"target": "red_apple", "location": "table"})
print(f"  put_down: {result}")
if "Put down" not in result: ok = False

result = sim.execute_action("move_to", {"x": 10, "y": 10, "z": 5})
print(f"  move_to: {result}")
if "moved to" not in result: ok = False

sim.close()
print(f"\n{'ALL PASSED' if ok else 'SOME FAILED'}")
