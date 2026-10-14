from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core.app.policy_observation_report import PolicyObservationTrace


class _Source:
    def __init__(self, dataset_dir: Path) -> None:
        self._dataset_dir = dataset_dir
        self.episode_id = 2
        self.fps = 30
        self.n_steps = 10
        self.current_task = "Pick up the carton"
        self._states = np.zeros((self.n_steps, 24), dtype=np.float32)
        self._actions = np.zeros((self.n_steps, 24), dtype=np.float32)

    def get_action_trajectory(self) -> np.ndarray:
        return self._actions.copy()

    def get_obs_state(self, index: int) -> np.ndarray:
        return self._states[index].copy()


def test_trace_saves_aligned_mse_and_plot(tmp_path: Path) -> None:
    source = _Source(tmp_path / "dataset")
    trace = PolicyObservationTrace(
        source,
        tmp_path / "results",
        publish_rate=10,
        gripper_open=0.0,
        gripper_close=-0.785,
        inference_horizon=2,
    )
    trace.add_chunk(0, np.ones((2, 24), dtype=np.float32))
    trace.record_command(0, np.zeros(24, dtype=np.float32))
    trace.record_command(1, np.zeros(24, dtype=np.float32))

    result = trace.save()

    assert result is not None
    assert Path(result["plot"]).is_file()
    metrics = json.loads(Path(result["metrics"]).read_text())
    assert metrics["source_action_indices"] == [0, 3]
    assert metrics["source_frames_per_inference"] == 6
    assert metrics["executed_actions"] == 2
    assert metrics["prediction_mse"] == 1.0
    assert metrics["command_mse"] == 0.0
    assert metrics["prediction_mse_by_joint"]["joint_1"] == 1.0
