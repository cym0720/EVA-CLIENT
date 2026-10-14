"""Protocol tests for the AgiBot G2 v1 EVA policy client."""

from __future__ import annotations

import numpy as np
import pytest
from openpi_client import msgpack_numpy

from core.app.handlers.control import limit_policy_qpos_step
from core.config import ConfigDict
from policy_client.agibot_g2_v1 import AgibotG2V1PolicyClient
from policy_client.base import PolicyRequestError


class _FakeConnection:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.sent: list[dict] = []

    def send(self, payload: bytes) -> None:
        self.sent.append(msgpack_numpy.unpackb(payload))

    def recv(self) -> bytes:
        return msgpack_numpy.packb(self.responses.pop(0))


def _client(connection: _FakeConnection) -> AgibotG2V1PolicyClient:
    return AgibotG2V1PolicyClient(
        "127.0.0.1",
        10093,
        client=(
            connection,
            {"protocol": "agibot_g2.v1", "action_chunk_size": 16},
        ),
    )


def _observation() -> dict:
    return {
        "images": {
            name: np.zeros((32, 48, 3), dtype=np.uint8)
            for name in ("top_head", "hand_left", "hand_right")
        },
        "state": np.zeros(24, dtype=np.float32),
        "prompt": "pick up the carton",
    }


def test_infer_adapts_three_camera_observation_and_action_chunk():
    actions = np.ones((1, 16, 24), dtype=np.float32)
    connection = _FakeConnection(
        [
            {
                "ok": True,
                "type": "inference_result",
                "request_id": "eva-infer-0",
                "data": {"actions": actions},
            }
        ]
    )

    result = _client(connection).infer(_observation())

    assert result["actions"].shape == (16, 24)
    assert result["actions"].dtype == np.float32
    request = connection.sent[0]
    assert request["type"] == "infer"
    assert request["payload"]["lang"] == "pick up the carton"
    assert set(request["payload"]["images"]) == {"top_head", "hand_left", "hand_right"}
    assert request["payload"]["state"].shape == (24,)


def test_reset_accepts_ack_without_echoed_request_id():
    connection = _FakeConnection([{"ok": True, "status": "ok"}])

    _client(connection).reset()

    assert connection.sent[0]["type"] == "reset"


def test_infer_rejects_missing_camera():
    connection = _FakeConnection([])
    observation = _observation()
    del observation["images"]["hand_right"]

    with pytest.raises(PolicyRequestError, match="missing camera"):
        _client(connection).infer(observation)


def test_policy_qpos_step_limit_caps_each_joint_delta():
    class _Transport:
        def get_latest_qpos(self):
            return np.array([0.0, 2.0, -1.0], dtype=np.float32)

    config = ConfigDict(
        inference_cfg=ConfigDict(max_policy_qpos_step=0.3),
    )
    runtime = type("Runtime", (), {"transport": _Transport()})()
    target = np.array([1.0, 1.0, 0.0], dtype=np.float32)

    limited = limit_policy_qpos_step(config, runtime, target)

    np.testing.assert_allclose(limited, [0.3, 1.7, -0.7], atol=1e-6)
