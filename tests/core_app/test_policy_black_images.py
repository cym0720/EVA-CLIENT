from __future__ import annotations

import numpy as np

from core.app.handlers.io import _policy_observation_for_inference


def test_black_policy_images_only_changes_inference_copy() -> None:
    top_head = np.full((4, 5, 3), 127, dtype=np.uint8)
    hand_left = np.full((2, 3, 3), 0.5, dtype=np.float32)
    observation = {
        "state": np.array([1.0, 2.0], dtype=np.float32),
        "images": {"top_head": top_head, "hand_left": hand_left},
        "prompt": "pick up the carton",
    }

    inference_observation = _policy_observation_for_inference(
        observation, black_images=True, is_dataset_observation=True
    )

    assert inference_observation is not observation
    assert inference_observation["state"] is observation["state"]
    assert inference_observation["prompt"] == observation["prompt"]
    assert inference_observation["images"] is not observation["images"]
    for name, original in observation["images"].items():
        black = inference_observation["images"][name]
        assert black.shape == original.shape
        assert black.dtype == original.dtype
        assert np.count_nonzero(black) == 0
    assert np.all(top_head == 127)
    assert np.all(hand_left == 0.5)


def test_black_policy_images_disabled_returns_original_observation() -> None:
    observation = {"images": {"top_head": np.ones((2, 2, 3), dtype=np.uint8)}}

    assert (
        _policy_observation_for_inference(
            observation, black_images=False, is_dataset_observation=True
        )
        is observation
    )


def test_black_policy_images_does_not_apply_to_live_observations() -> None:
    observation = {"images": {"top_head": np.ones((2, 2, 3), dtype=np.uint8)}}

    assert (
        _policy_observation_for_inference(
            observation, black_images=True, is_dataset_observation=False
        )
        is observation
    )
