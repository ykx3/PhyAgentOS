"""Tests for adaptive thinking routing.

Covers:
- Routing rule logic (_resolve_thinking_mode)
- Tool error detection (_is_tool_error)
- Periodic thinking check pattern (consecutive fast → thinking)
- Config integration
- Mock server integration (verifies correct models are called)
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from PhyAgentOS.config.schema import (
    AgentModes,
    Config,
    ModeConfig,
    ThinkingRoutingConfig,
)


#=====================================================================
# Unit tests for routing logic (no server needed)
# =====================================================================


class _FakeLoop:
    """Minimal stand-in for AgentLoop that exposes _resolve_thinking_mode."""

    def __init__(self, enabled: bool = True, max_consecutive_fast: int = 3):
        from PhyAgentOS.agent.loop import AgentLoop

        self._thinking_routing_enabled = enabled
        self._thinking_routing_config = ThinkingRoutingConfig(
            enabled=enabled,
            max_consecutive_fast=max_consecutive_fast,
            thinking_mode="thinking",
            fast_mode="fast",
        )
        # Bind the real method
        self._resolve_thinking_mode = AgentLoop._resolve_thinking_mode.__get__(self)


class TestResolveThinkingMode:
    """Test the _resolve_thinking_mode routing rules."""

    def test_disabled_returns_empty(self):
        loop = _FakeLoop(enabled=False)
        assert loop._resolve_thinking_mode(1, 0, False) == ""

    def test_first_iteration_uses_thinking(self):
        loop = _FakeLoop()
        assert loop._resolve_thinking_mode(1, 0, False) == "thinking"

    def test_second_iteration_no_error_uses_fast(self):
        loop = _FakeLoop()
        assert loop._resolve_thinking_mode(2, 0, False) == "fast"

    def test_error_triggers_thinking(self):
        loop = _FakeLoop()
        assert loop._resolve_thinking_mode(5, 2, True) == "thinking"

    def test_max_consecutive_fast_triggers_thinking(self):
        loop = _FakeLoop(max_consecutive_fast=3)
        assert loop._resolve_thinking_mode(5, 3, False) == "thinking"

    def test_below_max_consecutive_fast_stays_fast(self):
        loop = _FakeLoop(max_consecutive_fast=3)
        assert loop._resolve_thinking_mode(5, 2, False) == "fast"

    def test_error_priority_over_fast_count(self):
        """Error should trigger thinking even if below max_consecutive_fast."""
        loop = _FakeLoop(max_consecutive_fast=5)
        assert loop._resolve_thinking_mode(3, 1, True) == "thinking"

    def test_periodic_check_pattern(self):
        """Verify the full cycling pattern:
        thinking → fast×N → thinking → fast×N → ...
        """
        loop = _FakeLoop(max_consecutive_fast=2)

        modes: list[str] = []
        consecutive = 0
        for i in range(1, 10):
            mode = loop._resolve_thinking_mode(i, consecutive, False)
            modes.append(mode)
            if mode == "fast":
                consecutive += 1
            else:
                consecutive = 0

        assert modes == [
            "thinking",        # iteration 1 (user message)
            "fast", "fast",# iterations 2-3
            "thinking",        # iteration 4 (periodic check: consecutive_fast=2)
            "fast", "fast",    # iterations 5-6
            "thinking",        # iteration 7 (periodic check again)
            "fast", "fast",    # iterations 8-9
        ]

    def test_error_resets_consecutive_counter(self):
        """After an error triggers thinking, consecutive counter should reset."""
        loop = _FakeLoop(max_consecutive_fast=3)

        modes: list[str] = []
        consecutive = 0
        error_at_iteration = 4# simulate error on iteration 4

        for i in range(1, 9):
            has_error = (i == error_at_iteration)
            mode = loop._resolve_thinking_mode(i, consecutive, has_error)
            modes.append(mode)
            if mode == "fast":
                consecutive += 1
            else:
                consecutive = 0

        assert modes == [
            "thinking",              # iteration 1 (user message)
            "fast", "fast",          # iterations 2-3
            "thinking",              # iteration 4 (error!)
            "fast", "fast", "fast",  # iterations 5-7
            "thinking",              # iteration 8 (periodic: consecutive=3)
        ]


class TestIsToolError:
    """Test the _is_tool_error static method."""

    @staticmethod
    def _check(result):
        from PhyAgentOS.agent.loop import AgentLoop
        return AgentLoop._is_tool_error(result)

    def test_empty_result(self):
        assert self._check("") is False
        assert self._check(None) is False

    def test_error_colon_prefix(self):
        assert self._check("Error: something went wrong") is True

    def test_error_space_prefix(self):
        assert self._check("Error action rejected by Critic") is True

    def test_traceback(self):
        assert self._check("Traceback (most recent call last):\n  File ...") is True

    def test_normal_result(self):
        assert self._check("Action'move_to' validated and dispatched.") is False
        assert self._check("File content: hello world") is False
        assert self._check("ENVIRONMENT.md updated.") is False

    def test_case_insensitive(self):
        assert self._check("error: lowercase") is True
        assert self._check("ERROR: UPPERCASE") is True


# =====================================================================
# Config integration tests
# =====================================================================


class TestThinkingRoutingConfig:
    """Test configuration loading for thinking routing."""

    def test_default_config(self):
        config = ThinkingRoutingConfig()
        assert config.enabled is False
        assert config.max_consecutive_fast == 3
        assert config.thinking_mode == "thinking"
        assert config.fast_mode == "fast"

    def test_custom_config(self):
        config = ThinkingRoutingConfig(
            enabled=True,
            max_consecutive_fast=5,
            thinking_mode="deep",
            fast_mode="quick",
        )
        assert config.enabled is True
        assert config.max_consecutive_fast == 5
        assert config.thinking_mode == "deep"
        assert config.fast_mode == "quick"

    def test_agent_modes_includes_thinking_routing(self):
        modes = AgentModes(
            enabled=True,
            models={
                "thinking": ModeConfig(model="model-a", describe="Deep"),
                "fast": ModeConfig(model="model-b", describe="Quick"),
            },
            thinking_routing=ThinkingRoutingConfig(enabled=True),
        )
        assert modes.thinking_routing.enabled is True

    def test_providers_manager_stores_thinking_routing(self):
        from PhyAgentOS.providers.providers_manager import ProvidersManager

        tr = ThinkingRoutingConfig(enabled=True, max_consecutive_fast=5)
        pm = ProvidersManager(
            config=Config(),
            modes={"main": {"provider": MagicMock(), "describe": "Main"}},
            thinking_routing=tr,
        )
        assert pm.thinking_routing.enabled is True
        assert pm.thinking_routing.max_consecutive_fast == 5

    def test_providers_manager_default_routing(self):
        """When no thinking_routing is passed, defaults are used."""
        from PhyAgentOS.providers.providers_manager import ProvidersManager

        pm = ProvidersManager(
            config=Config(),
            modes={"main": {"provider": MagicMock(), "describe": "Main"}},
        )
        assert pm.thinking_routing.enabled is False


# =====================================================================
# Integration test with mock HTTP server
# =====================================================================


class TestThinkingRoutingWithMockServer:
    """Integration test using a real mock HTTP server with two models."""

    @pytest.fixture(autouse=True)
    def setup_server(self):
        """Start/stop mock LLM server for each test."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        from mock_llm_server import(
            MockLLMHandler,
            start_mock_server,
            stop_mock_server,
        )

        self.port = start_mock_server()
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self.handler = MockLLMHandler
        MockLLMHandler.reset_log()
        yield
        stop_mock_server()

    def _create_providers_manager(
        self, thinking_routing: ThinkingRoutingConfig | None = None
    ):
        """Create a ProvidersManager with thinking/fast modes pointing to mock server."""
        from PhyAgentOS.providers.custom_provider import CustomProvider
        from PhyAgentOS.providers.providers_manager import ProvidersManager

        thinking_provider = CustomProvider(
            api_key="mock-key",
            api_base=self.base_url,
            default_model="mock-thinking",
        )
        fast_provider = CustomProvider(
            api_key="mock-key",
            api_base=self.base_url,
            default_model="mock-fast",
        )

        modes = {
            "thinking": {"provider": thinking_provider, "describe": "Deep reasoning"},
            "fast": {"provider": fast_provider, "describe": "Quick responses"},
        }

        return ProvidersManager(
            config=Config(),
            modes=modes,
            default_mode="thinking",
            thinking_routing=thinking_routing
            or ThinkingRoutingConfig(enabled=True, max_consecutive_fast=2),
        )

    @pytest.mark.asyncio
    async def test_mode_routes_to_correct_model(self):
        """Verify that mode='thinking' hits mock-thinking and mode='fast' hits mock-fast."""
        pm = self._create_providers_manager()

        resp1 = await pm.chat_with_retry(
            messages=[{"role": "user", "content": "complex task"}],
            mode="thinking",
        )
        assert "thinking-model" in resp1.content

        resp2 = await pm.chat_with_retry(
            messages=[{"role": "user", "content": "simple feedback"}],
            mode="fast",
        )
        assert "fast-model" in resp2.content

        models = self.handler.get_model_sequence()
        assert models == ["mock-thinking", "mock-fast"]

    @pytest.mark.asyncio
    async def test_response_time_difference(self):
        """Fast model should respond significantly faster than thinking model."""
        pm = self._create_providers_manager()

        t0 = time.time()
        await pm.chat_with_retry(
            messages=[{"role": "user", "content": "test"}],
            mode="thinking",
        )
        thinking_time = time.time() - t0

        t0 = time.time()
        await pm.chat_with_retry(
            messages=[{"role": "user", "content": "test"}],
            mode="fast",
        )
        fast_time = time.time() - t0

        assert fast_time < thinking_time,(
            f"Fast ({fast_time:.3f}s) should be faster than thinking ({thinking_time:.3f}s)"
        )

    @pytest.mark.asyncio
    async def test_simulated_agent_loop_routing_pattern(self):
        """Simulate the agent loop's routing pattern and verify model sequence."""
        pm = self._create_providers_manager(
            ThinkingRoutingConfig(enabled=True, max_consecutive_fast=2)
        )
        loop = _FakeLoop(max_consecutive_fast=2)

        consecutive_fast = 0
        last_error = False

        for iteration in range(1, 8):
            mode = loop._resolve_thinking_mode(iteration, consecutive_fast, last_error)
            await pm.chat_with_retry(
                messages=[{"role": "user", "content": f"iteration {iteration}"}],
                mode=mode,
            )
            if mode == "fast":
                consecutive_fast += 1
            else:
                consecutive_fast = 0

        expected = [
            "mock-thinking",  # iter 1: user message
            "mock-fast",      # iter 2: fast
            "mock-fast",      # iter 3: fast
            "mock-thinking",  # iter 4: periodic check (consecutive=2)
            "mock-fast",      # iter 5: fast
            "mock-fast",      # iter 6: fast
            "mock-thinking",  # iter 7: periodic check (consecutive=2)
        ]
        assert self.handler.get_model_sequence() == expected
