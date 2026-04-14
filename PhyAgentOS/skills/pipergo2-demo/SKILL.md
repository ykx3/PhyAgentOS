---
name: pipergo2-demo
description: Deterministic demo mapping for open sim, go to desk, then pick-and-move.
metadata: {"PhyAgentOS":{"always":true},"nanobot":{"emoji":"🧪"}}
---

# PiperGo2 Demo Skill

This skill is a strict demo router for three sequential intents:

1. `open simulation`
2. `go to desk`
3. `pick up the red cube and move next to the rear pedestal`

## Preconditions

- HAL watchdog must already be running with:
  - driver: `pipergo2_manipulation`
  - driver-config: `examples/pipergo2_manipulation_driver.json` (or equivalent)
- If simulation may be cold, dispatch `enter_simulation` first.

## Intent Mapping (MUST follow)

### A) Open Simulation

When user input semantically means opening simulation (examples: `open simulation`, `start simulation`):

- call `execute_robot_action` with:
  - `action_type`: `enter_simulation`
  - `parameters`: `{}`
  - `reasoning`: short reason

### B) Go To Desk

When user input semantically means "go to desk" (examples: `go to desk`, `go near table`, `move to desk`):

- call `execute_robot_action` with:
  - `action_type`: `navigate_to_named`
  - `parameters`: `{"waypoint_key":"desk"}`
  - `reasoning`: short reason

### C) Pick Up And Move To Rear Pedestal

When user input semantically means "pick up the red cube and move next to the rear pedestal"
(examples: `pick up the red cube and move next to the rear pedestal`, `grab the red cube and go near the rear pedestal`):

- call `execute_robot_action` with:
  - `action_type`: `run_pick_place`
  - `parameters`: `{"target_color_cn":"red","execute_place":false}`
  - `reasoning`: short reason
- then call `execute_robot_action` with:
  - `action_type`: `navigate_to_waypoint`
  - `parameters`: `{"xy":[1.02,7.08]}`
  - `reasoning`: short reason

## Demo Safety Rules

- Never claim success without tool result confirmation.
- Treat HAL watchdog `Result:` semantics as source of truth.
- If tool returns `Error: API not started`, do **not** auto-start; explicitly ask user to run `open simulation` first.
- Keep responses short and operational for live demo.
