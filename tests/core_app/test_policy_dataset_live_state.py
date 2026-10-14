from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from core.app.handlers import io
from core.app.handlers.space import JointState
from core.app.state import RuntimeState
from core.config import ConfigDict, load_config


@pytest.fixture
def dataset_inference(monkeypatch):
    images = {
        name: np.tile(np.array([10, 20, 30], dtype=np.uint8), (2, 3, 1))
        for name in ("top_head", "hand_left", "hand_right")
    }
    frame = SimpleNamespace(images=images, state_qpos=np.full(24, -2, dtype=np.float32))
    source = SimpleNamespace(frame_index=0, fps=30, n_steps=144, get_frame=Mock(return_value=frame))
    source.seek = lambda index: setattr(source, "frame_index", index)
    transport = SimpleNamespace(
        create_rate=lambda _hz: SimpleNamespace(sleep=lambda: None),
        is_shutdown=lambda: False,
        get_frame=Mock(return_value=frame),
        get_latest_qpos=Mock(return_value=np.ones(24, dtype=np.float32)),
    )
    robot = SimpleNamespace(
        total_action_dim=24,
        snap_grippers=Mock(),
        build_observation=lambda **observation: observation,
    )
    policy = SimpleNamespace(
        infer=Mock(return_value={"actions": np.zeros((16, 24), dtype=np.float32)}),
        reset=Mock(),
    )
    runtime = RuntimeState(
        robot=robot,
        transport=transport,
        policy=policy,
        policy_metadata={"sequential_chunks": True, "chunk_size": 16},
        inference_observation_source=source,
        infer_strategy=SimpleNamespace(execute_horizon=16),
    )
    config = ConfigDict(
        transport=ConfigDict(
            convert_bgr_to_rgb=True,
            image_height=2,
            image_width=3,
            resize_pad=False,
            image_layout="hwc",
        ),
        inference_cfg=ConfigDict(
            publish_rate=10,
            obs_space=JointState(),
            action_space=JointState(),
            observation_dataset=ConfigDict(use_live_state=True),
        ),
        robot=ConfigDict(gripper_threshold=-0.4, gripper_open=0.0, gripper_close=-0.785),
    )
    monkeypatch.setattr(io, "submit_rl_critic", lambda *_args: None)
    return config, runtime, source, frame


@pytest.mark.parametrize("prebuilt", [False, True])
def test_dataset_images_are_sent_with_fresh_live_state(dataset_inference, prebuilt):
    config, runtime, source, frame = dataset_inference
    live_states = [np.arange(24, dtype=np.float64), np.arange(24, dtype=np.float64) + 10]
    runtime.transport.get_latest_qpos.side_effect = live_states
    observation = (
        {
            "images": {name: image[..., ::-1].copy() for name, image in frame.images.items()},
            "state": frame.state_qpos,
            "prompt": "pick up the carton",
        }
        if prebuilt
        else None
    )

    for _ in live_states:
        io.fetch_action_chunk(
            config, runtime, "pick up the carton", prebuilt_observation=observation
        )

    for call, live_state in zip(runtime.policy.infer.call_args_list, live_states, strict=True):
        sent = call.args[0]
        np.testing.assert_array_equal(sent["state"], live_state.astype(np.float32))
        assert sent["state"].dtype == np.float32
        assert not np.shares_memory(sent["state"], live_state)
        for name, image in frame.images.items():
            np.testing.assert_array_equal(sent["images"][name], image[..., ::-1])
        assert sent["prompt"] == "pick up the carton"
    np.testing.assert_array_equal(frame.state_qpos, np.full(24, -2, dtype=np.float32))
    assert source.frame_index == 96
    assert runtime.transport.get_latest_qpos.call_count == 2


def test_dataset_state_remains_default_when_live_state_option_is_absent(dataset_inference):
    config, runtime, _source, frame = dataset_inference
    del config.inference_cfg.observation_dataset["use_live_state"]

    io.fetch_action_chunk(config, runtime, "pick up the carton")

    np.testing.assert_array_equal(runtime.policy.infer.call_args.args[0]["state"], frame.state_qpos)
    runtime.transport.get_latest_qpos.assert_not_called()


@pytest.mark.parametrize("feedback", [None, np.ones(23), np.full(24, np.nan)])
def test_invalid_live_feedback_prevents_policy_request(dataset_inference, feedback):
    config, runtime, source, _frame = dataset_inference
    runtime.transport.get_latest_qpos.return_value = feedback

    with pytest.raises(RuntimeError, match="Live joint state"):
        io.fetch_action_chunk(config, runtime, "pick up the carton")

    runtime.policy.infer.assert_not_called()
    assert source.frame_index == 0


def test_live_state_option_only_applies_to_dataset_observations(dataset_inference):
    config, runtime, _source, frame = dataset_inference
    runtime.inference_observation_source = None

    io.fetch_action_chunk(config, runtime, "pick up the carton")

    np.testing.assert_array_equal(runtime.policy.infer.call_args.args[0]["state"], frame.state_qpos)
    runtime.transport.get_latest_qpos.assert_not_called()


@pytest.mark.parametrize("preset", ["agibot_g2_v1_dataset_ep2", "agibot_g2_v1_dataset_ep2_h20"])
def test_dataset_presets_enable_live_state_and_real_images(preset):
    config = load_config(f"configs/01_deploy/agibot_g2/{preset}.py")

    assert config.inference_cfg.observation_dataset.use_live_state is True
    assert config.inference_cfg.black_policy_images is False
