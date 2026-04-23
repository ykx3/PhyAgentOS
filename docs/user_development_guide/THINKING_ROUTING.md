# Adaptive Thinking Routing

## Overview

PhyAgentOS introduces **Adaptive Thinking Routing** — a dynamic model routing mechanism that intelligently switches between a deep-reasoning ("thinking") model and a lightweight ("fast") model during the agent loop. This reduces latency on routine operations while preserving deep reasoning capability for complex tasks.

### Problem Statement

In PhyAgentOS's dual-track architecture, the Agent (Track A) processes user commands through a multi-iteration tool-calling loop. Previously, every LLM call used the same model with the same reasoning depth, even for simple feedback processing like "tool returned OK, continue next step." When using reasoning-heavy models (e.g., DeepSeek-R1, Claude with extended thinking), this caused significant latency on operations that didn't need deep analysis.

### Solution: Fast-Slow Cognitive Architecture

Inspired by Daniel Kahneman's *Thinking, Fast and Slow*, the system now supports two cognitive modes:

| Mode | Purpose | Typical Models|
|------|---------|----------------|
| **Thinking** (System2) | Complex planning, error recovery, periodic sanity checks | `deepseek-reasoner`, `claude-sonnet-4-5`, `gpt-4o` |
| **Fast** (System 1) | Routine tool-loop continuation, simple validation | `deepseek-chat`, `claude-haiku-4-5`, `gpt-4o-mini` |

## Configuration

Add the following to your `~/.PhyAgentOS/config.json`:

```json
{
  "agents": {
    "modes": {
      "enabled": true,
      "defaultMode": "thinking",
      "models": {
        "thinking": {
          "model": "deepseek/deepseek-reasoner",
          "describe": "Deep reasoning model for complex planning and new tasks"
        },
        "fast": {
          "model": "deepseek/deepseek-chat",
          "describe": "Fast model for simple feedback, validation and routine operations"
        }
      },
      "thinkingRouting": {
        "enabled": true,
        "maxConsecutiveFast": 3
      }
    }
  }
}
```

### Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `thinkingRouting.enabled` | bool | `false` | Enable/disable adaptive routing |
| `thinkingRouting.maxConsecutiveFast` | int | `3` | After N consecutive fast calls, force one thinking call for periodic sanity check |
| `thinkingRouting.thinkingMode` | string | `"thinking"` | Mode name for the thinking model (must match a key in `models`) |
| `thinkingRouting.fastMode` | string | `"fast"` | Mode name for the fast model (must match a key in `models`) |

### Example Configurations

**DeepSeek (recommended for cost efficiency):**
```json
{
  "thinking": { "model": "deepseek/deepseek-reasoner", "describe": "R1 reasoning" },
  "fast": { "model": "deepseek/deepseek-chat", "describe": "V3 fast chat" }
}
```

**Anthropic Claude:**
```json
{
  "thinking": { "model": "anthropic/claude-sonnet-4-5", "describe": "Sonnet deep reasoning" },
  "fast": { "model": "anthropic/claude-haiku-4-5-20251001", "describe": "Haiku fast responses" }
}
```

**OpenAI:**
```json
{
  "thinking": { "model": "gpt-4o", "describe": "GPT-4o reasoning" },
  "fast": { "model": "gpt-4o-mini", "describe": "GPT-4o-mini fast" }
}
```

**Mixed providers (with OpenRouter):**
```json
{
  "thinking": { "model": "openrouter/deepseek/deepseek-r1", "describe": "R1 via OpenRouter" },
  "fast": { "model": "openrouter/deepseek/deepseek-chat-v3-0324", "describe": "V3 via OpenRouter" }
}
```

## Routing Rules

The routing engine applies these rules in priority order at each iteration of the agent loop:

```
+----------------------------------+
|  New user message received|
|  iteration == 1                  |------> THINKING
+----------------------------------+
                |
                v
+----------------------------------+
|  Previous tool returned error?|
|  _is_tool_error(result) == True  |------> THINKING
+----------------------------------+
                |
                v
+----------------------------------+
|  consecutive_fast >= threshold?  |
|  (periodic sanity check)         |------> THINKING
+----------------------------------+
                |
                v
+----------------------------------+
|  Default|------> FAST
+----------------------------------+
```

### Routing Timeline Example

With `maxConsecutiveFast = 3`:

```
Iteration  1: [THINKING] User says: "Look at the table and grab the apple"
Iteration  2: [FAST]Tool result: environment scanned -> OK
Iteration  3: [FAST]     Tool result: apple detected at (0.3, 0.5) -> OK
Iteration  4: [FAST]     Tool result: action dispatched -> OK
Iteration  5: [THINKING] <-- Periodic check (3 consecutive fast calls)
Iteration  6: [FAST]     Tool result: gripper closed -> OK
Iteration  7: [FAST]     Tool result: lift complete -> OK
Iteration  8: [FAST]     Tool result: task done -> OK
Iteration  9: [THINKING] <-- Periodic check again
```

If an error occurs at any point:

```
Iteration  6: [FAST]     Tool result: Error: gripper failed to close
Iteration  7: [THINKING] <-- Error recovery (regardless of consecutive count)
Iteration  8: [FAST]     Tool result: retry succeeded -> OK
```

## Affected Components

| Component | Behavior |
|-----------|----------|
| **Agent Loop** (`agent/loop.py`) | Routes each LLM call based on iteration context |
| **Critic** (`agent/tools/embodied.py`) | Always uses fast mode (VALID/INVALID is simple) |
| **Subagent** (`agent/subagent.py`) | Uses fast mode by default (focused subtasks) |

## Backward Compatibility

- **No `modes` configured**: System works exactly as before (single model)
- **`modes` configured but `thinkingRouting.enabled` is false**: Existing mode routing works unchanged
- **`thinkingRouting` enabled but modes missing**: Warning logged, falls back to single model

## Testing

A mock LLM server is provided for testing:

```bash
# Start the mock server (two models with different response delays)
python tests/mock_llm_server.py --port 18199

# Run the thinking routing tests
pytest tests/test_thinking_routing.py -v
```

The mock server provides two models:
- `mock-thinking`:1 second response delay
- `mock-fast`: 0.05 second response delay

## Architecture Design Notes

### Why Rule-Based Routing (Not LLM-Based)

The existing `ProvidersManager` has an `auto` mode that uses an LLM to decide which mode to use. For thinking routing, this would be counterproductive -- making an LLM call to decide whether to think deeply defeats the purpose of saving time. Instead, we use deterministic rules based on:

1. **Iteration number** -- first iteration always needs understanding
2. **Tool error status** -- errors need analysis
3. **Consecutive fast count** -- periodic sanity checks prevent drift

### Why Dual-Model (Not `reasoning_effort` Parameter)

While some models (Claude, DeepSeek-R1) support `reasoning_effort` parameters, this approach only works with specific providers. Dual-model routing is **provider-agnostic** -- users can pair any two models from any provider.

---

# 自适应思考路由（中文说明）

## 简介

PhyAgentOS 引入了**自适应思考路由**机制——在 Agent 循环中根据任务上下文动态切换深度思考模型和快速响应模型，在保证关键决策质量的同时显著降低常规操作的延迟。

## 配置方法

在 `~/.PhyAgentOS/config.json` 中配置两个模型：

```json
{
  "agents": {
    "modes": {
      "enabled": true,
      "models": {
        "thinking": { "model": "deepseek/deepseek-reasoner", "describe": "深度推理模型" },
        "fast": { "model": "deepseek/deepseek-chat", "describe": "快速响应模型" }
      },
      "thinkingRouting": {
        "enabled": true,
        "maxConsecutiveFast": 3
      }
    }
  }
}
```

## 路由规则

| 场景 | 使用模型 | 原因 |
|------|---------|------|
| 收到用户新消息 | Thinking | 需要理解意图、规划任务 |
| 工具返回正常结果 | Fast | 简单续接，无需深度分析 |
| 工具返回错误 | Thinking | 需要分析错误原因、调整策略 |
| 连续 N 次 Fast 后 | Thinking | 定期检查，防止偏离 |
| Critic 校验动作 | Fast | VALID/INVALID 判断简单 |
| 子 Agent 执行 | Fast | 子任务通常是聚焦的 |

## 运行测试

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_thinking_routing.py -v -p asyncio
```
