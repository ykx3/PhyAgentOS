# Robot Embodiment Declaration

This profile describes a remote `xlerobot_2wheels` controlled from OEA through ZMQ.
The physical robot host runs on Jetson Orin NX (`xlerobot_2wheels_host`), while OEA runs on a separate machine.

## Identity

- **Name**: XLerobot 2Wheels (Remote)
- **Type**: Differential-drive mobile base with dual arms, head, and dual grippers
- **Runtime Topology**: OEA/HAL on control host -> ZMQ -> Jetson Orin NX robot host

## Sensors

- **Camera**: Optional wrist/head camera stream forwarded by remote host
- **Joint feedback**: Position and wheel velocity feedback via host observation channel

## Supported Actions

| Action | Parameters | Description |
|--------|-----------|-------------|
| `connect_robot` | `robot_id` (optional) | Connect to remote ZMQ host and validate observation stream |
| `check_connection` | `robot_id` (optional) | Run health check by fetching latest observation |
| `disconnect_robot` | `robot_id` (optional) | Disconnect from remote host and stop base |
| `move_base` | `x_vel_m_s`, `theta_deg_s`, `duration_s`, `robot_id` (optional) | Send velocity command loop for fixed duration; auto-stop at end |
| `stop` | `robot_id` (optional) | Immediately send zero base velocity |
| `set_joint_targets` | `joints` object, `robot_id` (optional) | Send arm/head joint position targets (`*.pos`) |
| `set_gripper` | `side`, `value`, `robot_id` (optional) | Set left or right gripper target value |

## Physical Constraints

- **Base type**: 2-wheel differential drive (no lateral `y` motion)
- **Safety policy**:
  - `move_base.duration_s` is bounded by driver safety limit
  - Base is automatically stopped after `move_base`
  - Any action failure triggers a best-effort base stop
- **Speed limits**: configured by driver env vars and enforced before command dispatch

## Connection

- **Transport**: ZMQ (PUSH/PULL)
- **Preferred driver config keys**: `remote_ip`, `cmd_port`, `obs_port`, `robot_id`, `loop_hz`, `max_move_duration_s`, `safe_max_linear_m_s`, `safe_max_angular_deg_s`, `reconnect_policy`
- **Watchdog CLI**: `python hal/hal_watchdog.py --driver xlerobot_2wheels_remote --driver-config <json>`
- **Environment fallback**: `OEA_XLEROBOT_REMOTE_IP`, `OEA_XLEROBOT_CMD_PORT`, `OEA_XLEROBOT_OBS_PORT`, `OEA_XLEROBOT_ROBOT_ID`, `OEA_XLEROBOT_LOOP_HZ`, `OEA_XLEROBOT_MAX_MOVE_DURATION_S`, `OEA_XLEROBOT_SAFE_MAX_LINEAR_M_S`, `OEA_XLEROBOT_SAFE_MAX_ANGULAR_DEG_S`
- **Reconnect Policy**: auto by default in health checks

## Runtime Protocol

- **Connection channel**: `robots.<robot_id>.connection_state`
- **Pose channel**: `robots.<robot_id>.robot_pose` (placeholder pose with heartbeat timestamp)
- **Navigation channel**: `robots.<robot_id>.nav_state` (velocity-control status)
- **Health owner**: `hal_watchdog.py` via periodic `health_check()`

## Notes

- This profile intentionally exposes only atomic control actions.
- Higher-level behaviors (semantic navigation, visual servoing, grasp state machines) should be composed by OEA workflow layers, not embedded in this HAL driver.
