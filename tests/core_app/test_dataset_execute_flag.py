from __future__ import annotations

import sys

import main
from core.app.run import _dispatch_run
from core.app.state import RuntimeState, SessionMode, SessionState
from core.config import ConfigDict


def test_cli_execute_flag_controls_dataset_hardware_permission(monkeypatch) -> None:
    config = ConfigDict(inference_cfg=ConfigDict(observation_dataset=ConfigDict()))
    monkeypatch.setattr(main, "load_config", lambda _path: config)

    monkeypatch.setattr(sys, "argv", ["eva", "--config", "dataset.py"])
    main.parse_args()
    assert config.inference_cfg.observation_dataset["execute"] is False

    monkeypatch.setattr(sys, "argv", ["eva", "--config", "dataset.py", "--execute"])
    main.parse_args()
    assert config.inference_cfg.observation_dataset["execute"] is True


def test_dataset_policy_cannot_run_on_real_without_execute_flag() -> None:
    config = ConfigDict(
        inference_cfg=ConfigDict(observation_dataset=ConfigDict(execute=False))
    )
    runtime = RuntimeState(
        robot=object(), transport=object(), inference_observation_source=object()
    )
    session = SessionState(mode=SessionMode.REAL)

    _dispatch_run(config, runtime, session)

    assert session.last_error == "Real execution is disabled; relaunch EVA with --execute"
