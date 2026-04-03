"""Lightweight ZMQ client for the XLerobot2Wheels remote host protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import zmq


@dataclass(frozen=True)
class XLerobot2WheelsRemoteClientConfig:
    """Connection settings for the remote XLerobot2Wheels host."""

    id: str
    remote_ip: str
    port_zmq_cmd: int = 5555
    port_zmq_observations: int = 5556
    polling_timeout_ms: int = 15
    connect_timeout_s: int = 5


class XLerobot2WheelsRemoteClient:
    """Tiny OEA-owned client that speaks the Jetson host's ZMQ JSON protocol."""

    def __init__(self, config: XLerobot2WheelsRemoteClientConfig):
        self.config = config
        self.id = config.id
        self.remote_ip = config.remote_ip
        self.port_zmq_cmd = config.port_zmq_cmd
        self.port_zmq_observations = config.port_zmq_observations
        self.polling_timeout_ms = config.polling_timeout_ms
        self.connect_timeout_s = config.connect_timeout_s

        self._context: zmq.Context | None = None
        self._cmd_socket: zmq.Socket | None = None
        self._observation_socket: zmq.Socket | None = None
        self._is_connected = False
        self._last_observation: dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def connect(self) -> None:
        if self._is_connected:
            raise RuntimeError(
                "XLerobot2Wheels remote client is already connected. "
                "Do not call connect() twice."
            )

        self._context = zmq.Context()

        self._cmd_socket = self._context.socket(zmq.PUSH)
        self._cmd_socket.connect(f"tcp://{self.remote_ip}:{self.port_zmq_cmd}")
        self._cmd_socket.setsockopt(zmq.CONFLATE, 1)

        self._observation_socket = self._context.socket(zmq.PULL)
        self._observation_socket.connect(
            f"tcp://{self.remote_ip}:{self.port_zmq_observations}"
        )
        self._observation_socket.setsockopt(zmq.CONFLATE, 1)

        try:
            self._last_observation = self._poll_latest_observation(
                timeout_ms=int(self.connect_timeout_s * 1000),
                allow_cached=False,
            )
        except Exception:
            self.disconnect(silent=True)
            raise

        self._is_connected = True

    def get_observation(self) -> dict[str, Any]:
        if not self._is_connected:
            raise RuntimeError(
                "XLerobot2Wheels remote client is not connected. "
                "Call connect() first."
            )
        observation = self._poll_latest_observation(
            timeout_ms=self.polling_timeout_ms,
            allow_cached=True,
        )
        self._last_observation = observation
        return dict(observation)

    def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
        if not self._is_connected or self._cmd_socket is None:
            raise RuntimeError(
                "XLerobot2Wheels remote client is not connected. "
                "Call connect() first."
            )
        self._cmd_socket.send_string(json.dumps(action))
        return dict(action)

    def disconnect(self, *, silent: bool = False) -> None:
        if not self._is_connected and not silent:
            raise RuntimeError(
                "XLerobot2Wheels remote client is not connected. "
                "Call connect() before disconnect()."
            )

        if self._observation_socket is not None:
            self._observation_socket.close()
            self._observation_socket = None
        if self._cmd_socket is not None:
            self._cmd_socket.close()
            self._cmd_socket = None
        if self._context is not None:
            self._context.term()
            self._context = None
        self._is_connected = False

    def _poll_latest_observation(
        self,
        *,
        timeout_ms: int,
        allow_cached: bool,
    ) -> dict[str, Any]:
        if self._observation_socket is None:
            raise RuntimeError("Observation socket is not initialized.")

        poller = zmq.Poller()
        poller.register(self._observation_socket, zmq.POLLIN)
        socks = dict(poller.poll(timeout_ms))
        if self._observation_socket not in socks:
            if allow_cached and self._last_observation:
                return dict(self._last_observation)
            raise RuntimeError(
                "Timeout waiting for XLerobot2Wheels Host to connect expired."
            )

        last_message: str | None = None
        while True:
            try:
                last_message = self._observation_socket.recv_string(zmq.NOBLOCK)
            except zmq.Again:
                break

        if last_message is None:
            if allow_cached and self._last_observation:
                return dict(self._last_observation)
            raise RuntimeError("Observation poll indicated data but no message was received.")

        try:
            payload = json.loads(last_message)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Failed to decode observation JSON from XLerobot2Wheels Host.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Observation payload from XLerobot2Wheels Host must be a JSON object.")
        return payload
