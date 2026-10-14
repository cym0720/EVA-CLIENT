"""AgiBot G2 deployment for the stateful agibot_g2.v1 policy server."""

_base_ = ["_base.py"]

transport = dict(
    type="zmq",
    resize_pad=False,
    image_layout="hwc",
)

policy = dict(
    type="agibot_g2_v1",
    host="127.0.0.1",
    port=11093,
)

inference_cfg = dict(
    publish_rate=10,
    max_run_frames=300,
    max_policy_qpos_step=0.3,
    setup_warmup_chunks=1,
    debug_tasks=["Pick up the white carton and place it inside the large tan carton on the left."],
)

inference_strategies = dict(
    _delete_=True,
    sync=dict(
        type="BaseInferStrategy",
        args=dict(execute_horizon=16),
    ),
)
