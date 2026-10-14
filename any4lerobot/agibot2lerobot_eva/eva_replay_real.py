"""Replay the converted G2 dataset through the live G2 ZMQ transport.

This config publishes replay actions to the real robot when ``run`` is started.
The G2 hardware node must already be connected at the configured ZMQ endpoints.
"""

_base_ = ["../../configs/01_deploy/agibot_g2/_base.py"]

console = dict(initial_tab="replay")
policy = dict(type="replay")
control_channel = dict(enabled=True, host="127.0.0.1", port=15757)
inference_cfg = dict(publish_rate=30, setup_warmup_chunks=0)
replay_cfg = dict(reset_to_home=False, start_transition_steps=150)
