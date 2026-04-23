"""Mock OpenAI-compatible LLM server for testing thinking routing.

Two mock models:
- mock-thinking: Simulates deep reasoning (1s response time)
- mock-fast: Simulates quick responses (0.05s response time)

Usage:
    # As standalone server:
    python tests/mock_llm_server.py --port 18199

    # In tests:
    from tests.mock_llm_server import start_mock_server, stop_mock_server
    port = start_mock_server()
    # ... run tests ...
    stop_mock_server()
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

THINKING_DELAY = 1.0  # seconds
FAST_DELAY = 0.05  # seconds

_server: HTTPServer | None = None
_thread: threading.Thread | None = None


class MockLLMHandler(BaseHTTPRequestHandler):
    """Handles /v1/chat/completions requests with model-based routing."""

    # Shared call history for test assertions
    call_log: list[dict] = []

    def log_message(self, format, *args):
        """Suppress HTTP logs during tests."""
        pass

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404, "Only /v1/chat/completions is supported")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        model = body.get("model", "unknown")
        messages = body.get("messages", [])
        tools = body.get("tools")

        # Log the call for test assertions
        MockLLMHandler.call_log.append({
            "model": model,
            "message_count": len(messages),
            "has_tools": tools is not None,
            "timestamp": time.time(),
        })

        # Route based on model name — "thinking" models are slow, others are fast
        if "thinking" in model.lower():
            time.sleep(THINKING_DELAY)
            content = f"[thinking-model] Deep analysis of the task is complete. Model: {model}."
        else:
            time.sleep(FAST_DELAY)
            content = f"[fast-model] Quick response generated. Model: {model}."

        # Build OpenAI-compatible response
        response = {
            "id": f"mock-{len(MockLLMHandler.call_log)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }

        response_bytes = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    @classmethod
    def reset_log(cls):
        """Clear the call history."""
        cls.call_log.clear()

    @classmethod
    def get_model_sequence(cls) -> list[str]:
        """Return the sequence of models called (for easy assertion)."""
        return [entry["model"] for entry in cls.call_log]


def start_mock_server(port: int = 0) -> int:
    """Start the mock server in a background thread.

    Args:
        port: Port to listen on.0 means auto-assign.

    Returns:
        The actual port the server is listening on.
    """
    global _server, _thread
    _server = HTTPServer(("127.0.0.1", port), MockLLMHandler)
    actual_port = _server.server_address[1]
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    MockLLMHandler.reset_log()
    return actual_port


def stop_mock_server():
    """Stop the mock server and wait for thread to finish."""
    global _server, _thread
    if _server:
        _server.shutdown()
        _server = None
    if _thread:
        _thread.join(timeout=5)
        _thread = None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock LLM server for testing")
    parser.add_argument("--port", type=int, default=18199, help="Port to listen on")
    args = parser.parse_args()

    port = start_mock_server(args.port)
    print(f"Mock LLM server running at http://127.0.0.1:{port}/v1/chat/completions")
    print(f"Models: mock-thinking (delay={THINKING_DELAY}s), mock-fast (delay={FAST_DELAY}s)")
    print("Press Ctrl+C to stop.")
    try:
        _thread.join()
    except KeyboardInterrupt:
        print("\nShutting down...")
        stop_mock_server()
